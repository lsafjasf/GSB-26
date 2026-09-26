"""cleanup_framework 自测。

运行：python3 -m unittest -v test_cleanup
     或 python3 test_cleanup.py
"""

import json
import os
import signal
import sys
import tempfile
import time
import unittest

from cleanup_framework import (
    CleanupError,
    CleanupStack,
    signal_cleanup,
)


class Interrupt(Exception):
    """模拟注入的中断（等价于信号/取消）。"""


# ---------------------------------------------------------------- 顺序与依赖

class TestOrderAndDependencies(unittest.TestCase):
    def test_reverse_registration_order(self):
        calls = []
        stack = CleanupStack()
        for i in range(5):
            stack.register(f"a{i}", lambda i=i: calls.append(i))
        report = stack.cleanup()
        self.assertTrue(report.ok)
        self.assertEqual(calls, [4, 3, 2, 1, 0])  # 逆序

    def test_dependency_overrides_reverse_order(self):
        # flush 先注册，按纯逆序会后执行；depends_on 强制它先清理
        calls = []
        stack = CleanupStack()
        stack.register("flush-buffer", lambda: calls.append("flush"))
        stack.register("commit-log", lambda: calls.append("commit"),
                       depends_on=["flush-buffer"])
        stack.register("close-db", lambda: calls.append("close"))
        stack.cleanup()
        self.assertEqual(calls, ["close", "flush", "commit"])

    def test_unknown_dependency_raises(self):
        stack = CleanupStack()
        stack.register("x", lambda: None, depends_on=["ghost"])
        with self.assertRaises(CleanupError):
            stack.cleanup()

    def test_dependency_cycle_raises(self):
        stack = CleanupStack()
        stack.register("a", lambda: None, depends_on=["b"])
        stack.register("b", lambda: None, depends_on=["a"])
        with self.assertRaises(CleanupError):
            stack.cleanup()

    def test_duplicate_name_raises(self):
        stack = CleanupStack()
        stack.register("x", lambda: None)
        with self.assertRaises(CleanupError):
            stack.register("x", lambda: None)

    def test_unregister_skips_action(self):
        calls = []
        stack = CleanupStack()
        stack.register("keep", lambda: calls.append("keep"))
        stack.register("handoff", lambda: calls.append("handoff"))
        stack.unregister("handoff")  # 资源已移交，不再清理
        stack.cleanup()
        self.assertEqual(calls, ["keep"])


# ---------------------------------------------------------------- 重入与幂等

class TestReentrancy(unittest.TestCase):
    def test_cleanup_twice_runs_actions_once(self):
        calls = []
        stack = CleanupStack()
        stack.register("once", lambda: calls.append(1))
        r1 = stack.cleanup()
        r2 = stack.cleanup()  # 第二次不得重复执行
        self.assertIs(r1, r2)
        self.assertEqual(calls, [1])

    def test_reentrant_cleanup_inside_action(self):
        # 清理动作内部（如信号处理器）再次调用 cleanup：不递归、不二次释放
        calls = []
        stack = CleanupStack()
        reentrant_report = []

        def first():
            calls.append("first")
            reentrant_report.append(stack.cleanup())  # 重入 -> None

        stack.register("first", first)
        stack.register("second", lambda: calls.append("second"))
        report = stack.cleanup()
        self.assertEqual(reentrant_report, [None])   # 重入调用立即返回
        self.assertEqual(calls, ["second", "first"])  # 各执行且仅执行一次
        self.assertTrue(report.ok)

    def test_register_after_cleanup_raises(self):
        stack = CleanupStack()
        stack.cleanup()
        with self.assertRaises(CleanupError):
            stack.register("late", lambda: None)


# ---------------------------------------------------------------- 失败汇总

class TestFailureReporting(unittest.TestCase):
    def test_failure_recorded_and_cleanup_continues(self):
        calls = []
        stack = CleanupStack()
        stack.register("ok-1", lambda: calls.append("ok-1"))

        def boom():
            calls.append("boom")
            raise RuntimeError("disk gone")

        stack.register("bad", boom)
        stack.register("ok-2", lambda: calls.append("ok-2"))
        report = stack.cleanup()
        # 三个动作都被尝试，顺序仍是逆序
        self.assertEqual(calls, ["ok-2", "boom", "ok-1"])
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.failures[0].name, "bad")
        self.assertIsInstance(report.failures[0].error, RuntimeError)
        self.assertIn("disk gone", report.failures[0].traceback_str)
        summary = report.summary()
        self.assertIn("1 failed", summary)
        self.assertIn("bad", summary)

    def test_multiple_failures_all_reported(self):
        stack = CleanupStack()
        for i in range(4):
            stack.register(f"f{i}", lambda i=i: (_ for _ in ()).throw(
                ValueError(i)))
        report = stack.cleanup()
        self.assertEqual(len(report.failures), 4)
        self.assertEqual([r.name for r in report.failures],
                         ["f3", "f2", "f1", "f0"])


# ------------------------------------------------------- 逐阶段中断注入测试

class LongTask:
    """模拟多阶段长任务：每阶段创建临时文件并推进状态文件。

    不变量（任意阶段被中断后都必须成立）：
    1. 临时目录下没有任何 .tmp 残留；
    2. 每个已创建资源的清理动作恰好执行一次（cleanup_log 无重复）；
    3. 状态文件只包含完整提交的阶段记录，无半成品；
    4. 清理顺序为资源创建的逆序。
    """

    STAGES = 6

    def __init__(self, workdir, interrupt_after=None):
        self.workdir = workdir
        self.interrupt_after = interrupt_after
        self.stack = CleanupStack()
        self.cleanup_log = []
        self.state_path = os.path.join(workdir, "state.json")

    def _make_cleanup(self, path, stage):
        def cleanup():
            if os.path.exists(path):
                os.remove(path)
            self.cleanup_log.append(stage)
        return cleanup

    def _stage(self, i):
        tmp = os.path.join(self.workdir, f"stage-{i}.tmp")
        with open(tmp, "w") as f:
            f.write(f"payload-{i}")
        self.stack.register(f"remove-stage-{i}", self._make_cleanup(tmp, i))
        # 状态文件只记录“完整完成”的阶段
        state = []
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                state = json.load(f)
        state.append({"stage": i, "done": True})
        with open(self.state_path, "w") as f:
            json.dump(state, f)

    def run(self):
        with self.stack:  # 退出（含异常）时自动清理
            for i in range(self.STAGES):
                self._stage(i)
                if self.interrupt_after == i:
                    raise Interrupt(f"injected after stage {i}")
        return self.stack.cleanup()


class TestStageByStageInterruption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _assert_consistent(self, task, completed_stages, interrupted):
        leftovers = [f for f in os.listdir(task.workdir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"残留临时文件: {leftovers}")
        # 每个清理动作恰好一次，且为创建逆序
        expected = list(reversed(range(completed_stages)))
        self.assertEqual(task.cleanup_log, expected)
        # 状态文件只含完整阶段
        with open(task.state_path) as f:
            state = json.load(f)
        self.assertEqual([s["stage"] for s in state],
                         list(range(completed_stages)))
        self.assertTrue(all(s["done"] for s in state))
        self.assertEqual(task.stack.pending_names(), [])

    def test_no_interrupt(self):
        task = LongTask(self.tmp.name)
        report = task.run()
        self.assertTrue(report.ok)
        self._assert_consistent(task, LongTask.STAGES, interrupted=False)

    def test_interrupt_at_every_stage(self):
        # 在每一步之后注入中断，逐个阶段验证清理完整且状态一致
        for k in range(LongTask.STAGES):
            with self.subTest(interrupt_after=k):
                workdir = os.path.join(self.tmp.name, f"case-{k}")
                os.makedirs(workdir)
                task = LongTask(workdir, interrupt_after=k)
                with self.assertRaises(Interrupt):
                    task.run()
                self._assert_consistent(task, k + 1, interrupted=True)

    def test_interrupt_before_any_stage(self):
        task = LongTask(self.tmp.name, interrupt_after=-1)
        # interrupt_after=-1 不匹配任何阶段，这里直接模拟“开始即中断”
        with self.assertRaises(Interrupt):
            with task.stack:
                raise Interrupt("before stage 0")
        self.assertEqual(task.cleanup_log, [])
        self.assertEqual(task.stack.pending_names(), [])


# ---------------------------------------------------------------- 信号集成

@unittest.skipUnless(hasattr(signal, "SIGINT"), "需要信号支持")
class TestSignalIntegration(unittest.TestCase):
    def test_sigint_triggers_cleanup_mid_task(self):
        stack = CleanupStack()
        cleaned = []
        stack.register("release", lambda: cleaned.append("release"))
        with self.assertRaises(InterruptedError):
            with signal_cleanup(stack):
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(1)  # 不应到达
        self.assertEqual(cleaned, ["release"])
        # 信号处理器再次触发 cleanup 也安全（幂等）
        self.assertIsNotNone(stack.cleanup())
        self.assertEqual(cleaned, ["release"])


# ------------------------------------------------------- 规模与耗时数据

class TestScaleAndTiming(unittest.TestCase):
    def test_no_resources(self):
        report = CleanupStack().cleanup()
        self.assertTrue(report.ok)
        self.assertEqual(report.records, [])

    def test_single_resource(self):
        calls = []
        stack = CleanupStack()
        stack.register("only", lambda: calls.append(1))
        report = stack.cleanup()
        self.assertEqual(calls, [1])
        self.assertEqual(len(report.records), 1)
        self.assertGreaterEqual(report.records[0].duration, 0)

    def test_many_resources_timing(self):
        for n in (1_000, 10_000, 50_000):
            stack = CleanupStack()
            sink = []
            for i in range(n):
                stack.register(f"r{i}", lambda: sink.append(1))
            t0 = time.perf_counter()
            report = stack.cleanup()
            wall = time.perf_counter() - t0
            self.assertTrue(report.ok)
            self.assertEqual(len(report.records), n)
            self.assertEqual(len(sink), n)
            print(f"\n[timing] N={n:>6}: total={report.total_duration*1e3:8.2f} ms, "
                  f"avg={report.total_duration/n*1e6:6.2f} us/action, "
                  f"wall={wall*1e3:8.2f} ms", file=sys.stderr)

    def test_many_resources_with_failures_timing(self):
        n = 10_000
        stack = CleanupStack()
        for i in range(n):
            if i % 10 == 0:
                stack.register(f"bad-{i}", lambda: 1 / 0)
            else:
                stack.register(f"ok-{i}", lambda: None)
        report = stack.cleanup()
        self.assertEqual(len(report.failures), n // 10)
        self.assertEqual(len(report.succeeded), n - n // 10)
        print(f"\n[timing] N={n} (10% failing): "
              f"total={report.total_duration*1e3:.2f} ms, "
              f"failures={len(report.failures)}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
