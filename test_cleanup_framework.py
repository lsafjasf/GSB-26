"""cleanup_framework 的自测套件（仅标准库 unittest）。

覆盖：
- 无资源 / 单资源 / 大量资源
- LIFO 顺序与 depends_on 依赖顺序
- 幂等（同一动作触发两次）与重入（清理动作内调用 close）
- 清理失败记录 + 继续执行 + 汇总报告
- 逐阶段中断注入：在流水线每一步之后注入中断，验证清理完整且状态一致
- 信号（SIGINT）触发清理
"""

import os
import shutil
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cleanup_framework import (
    CleanupReport,
    CleanupStack,
    StackClosedError,
    signal_cleanup,
)


class InjectedInterrupt(Exception):
    """测试注入的中断（模拟信号/取消）。"""


# ---------------------------------------------------------------- 基础行为

class TestBasicOrdering(unittest.TestCase):
    def test_empty_stack(self):
        """无资源：close 正常，报告为空。"""
        stack = CleanupStack()
        report = stack.close()
        self.assertTrue(report.ok)
        self.assertEqual(report.executed, [])
        self.assertEqual(report.failures, [])
        self.assertTrue(stack.closed)

    def test_single_resource(self):
        calls = []
        stack = CleanupStack()
        stack.register("only", lambda: calls.append("only"))
        report = stack.close()
        self.assertEqual(calls, ["only"])
        self.assertEqual(report.executed, ["only"])

    def test_lifo_order(self):
        """无依赖时严格逆注册序执行。"""
        calls = []
        stack = CleanupStack()
        for name in ["a", "b", "c", "d"]:
            stack.register(name, lambda n=name: calls.append(n))
        report = stack.close()
        self.assertEqual(report.executed, ["d", "c", "b", "a"])
        self.assertEqual(calls, ["d", "c", "b", "a"])

    def test_dependency_overrides_lifo(self):
        """depends_on 优先于 LIFO：被依赖者后清理。"""
        calls = []
        stack = CleanupStack()
        stack.register("lock", lambda: calls.append("lock"))
        stack.register("file", lambda: calls.append("file"), depends_on=["lock"])
        stack.register("net", lambda: calls.append("net"))
        # LIFO 本应是 net, file, lock；依赖要求 file 在 lock 前（已满足），
        # 但 net 注册最晚仍最先；再验证一个反例：
        report = stack.close()
        self.assertLess(report.executed.index("file"), report.executed.index("lock"))

    def test_dependency_forces_reorder(self):
        """依赖关系能翻转 LIFO 顺序（允许前向引用尚未注册的名字）。"""
        calls = []
        stack = CleanupStack()
        # LIFO 本应是 second, first；声明 first 依赖 second => first 先清理
        stack.register("first", lambda: calls.append("first"), depends_on=["second"])
        stack.register("second", lambda: calls.append("second"))
        report = stack.close()
        self.assertEqual(report.executed, ["first", "second"])

    def test_dependency_chain(self):
        """链式依赖 a -> b -> c：清理顺序 a, b, c。"""
        stack = CleanupStack()
        stack.register("c", lambda: None)
        stack.register("b", lambda: None, depends_on=["c"])
        stack.register("a", lambda: None, depends_on=["b"])
        report = stack.close()
        self.assertEqual(report.executed, ["a", "b", "c"])

    def test_duplicate_name_rejected(self):
        stack = CleanupStack()
        stack.register("x", lambda: None)
        with self.assertRaises(ValueError):
            stack.register("x", lambda: None)

    def test_register_after_close_rejected(self):
        stack = CleanupStack()
        stack.close()
        with self.assertRaises(StackClosedError):
            stack.register("late", lambda: None)

    def test_context_manager(self):
        calls = []
        with CleanupStack() as stack:
            stack.register("r", lambda: calls.append("r"))
        self.assertEqual(calls, ["r"])


# ------------------------------------------------------- 幂等 / 可重入

class TestIdempotentAndReentrant(unittest.TestCase):
    def test_double_close_runs_actions_once(self):
        """同一清理动作被触发两次：不报错、不二次执行。"""
        calls = []
        stack = CleanupStack()
        stack.register("a", lambda: calls.append("a"))
        stack.register("b", lambda: calls.append("b"))
        r1 = stack.close()
        r2 = stack.close()  # 第二次触发
        self.assertEqual(calls, ["b", "a"])          # 只执行一次
        self.assertIs(r1, r2)                        # 返回同一报告
        r3 = stack.close()
        self.assertEqual(calls, ["b", "a"])

    def test_reentrant_close_inside_action(self):
        """清理动作内部调用 close()：安全忽略，不破坏状态。"""
        calls = []
        stack = CleanupStack()
        inner_reports = []

        def action_b():
            calls.append("b")
            inner_reports.append(stack.close())  # 重入
            calls.append("b-done")

        stack.register("a", lambda: calls.append("a"))
        stack.register("b", action_b)
        report = stack.close()
        self.assertEqual(calls, ["b", "b-done", "a"])
        self.assertEqual(inner_reports, [None])  # 重入调用返回 None
        self.assertTrue(report.ok)

    def test_action_triggered_twice_directly(self):
        """模拟框架外重复触发：done 标志保证 fn 只跑一次。"""
        calls = []
        stack = CleanupStack()
        stack.register("x", lambda: calls.append("x"))
        stack.close()
        # 即使外部再次拿到 action 也无法通过框架重复执行
        stack.close()
        self.assertEqual(calls, ["x"])


# ------------------------------------------------------- 失败处理

class TestFailureHandling(unittest.TestCase):
    def test_failure_recorded_and_cleanup_continues(self):
        """中间动作抛异常：记录失败，后续清理照常执行。"""
        calls = []
        stack = CleanupStack()
        stack.register("ok1", lambda: calls.append("ok1"))

        def boom():
            calls.append("boom")
            raise RuntimeError("disk gone")

        stack.register("bad", boom)
        stack.register("ok2", lambda: calls.append("ok2"))
        report = stack.close()

        self.assertEqual(calls, ["ok2", "boom", "ok1"])  # 全部尝试，LIFO 顺序
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failures), 1)
        failure = report.failures[0]
        self.assertEqual(failure.name, "bad")
        self.assertIsInstance(failure.error, RuntimeError)
        self.assertIn("disk gone", failure.traceback)
        self.assertIn("FAILED bad", report.summary())

    def test_multiple_failures_all_reported(self):
        stack = CleanupStack()
        for i in range(3):
            def make(i):
                def fn():
                    raise ValueError(f"e{i}")
                return fn
            stack.register(f"f{i}", make(i))
        stack.register("good", lambda: None)
        report = stack.close()
        self.assertEqual(len(report.failures), 3)
        self.assertEqual({f.name for f in report.failures}, {"f0", "f1", "f2"})
        self.assertIn("good", report.executed)

    def test_unknown_dependency_reported_not_raised(self):
        calls = []
        stack = CleanupStack()
        stack.register("x", lambda: calls.append("x"), depends_on=["ghost"])
        report = stack.close()
        self.assertEqual(calls, ["x"])  # 清理仍然发生
        self.assertEqual(len(report.failures), 1)
        self.assertFalse(report.ok)

    def test_cycle_falls_back_to_lifo(self):
        calls = []
        stack = CleanupStack()
        stack.register("a", lambda: calls.append("a"), depends_on=["b"])
        stack.register("b", lambda: calls.append("b"), depends_on=["a"])
        report = stack.close()
        self.assertEqual(sorted(calls), ["a", "b"])  # 都执行了
        self.assertTrue(any("cycle" in str(f.error) for f in report.failures))


# ------------------------------------------- 逐阶段中断注入（核心测试）

class Pipeline:
    """模拟一个多阶段长任务：每阶段创建临时文件并注册清理。"""

    STEPS = 6

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pipeline-")
        self.stack = CleanupStack()
        self.created = []      # 已创建的临时文件
        self.cleaned = []      # 清理动作实际执行记录
        self.committed = False # 业务是否走到最终提交点

    def step(self, i):
        path = os.path.join(self.tmpdir, f"stage{i}.part")
        with open(path, "w") as f:
            f.write(f"partial-{i}")
        self.created.append(path)

        def cleanup(p=path, i=i):
            # 清理动作：删除半成品文件；依赖前面阶段的锁
            if os.path.exists(p):
                os.remove(p)
            self.cleaned.append(i)

        depends = ["lock"] if i > 0 else []
        if i == 0:
            self.stack.register("lock", lambda: self.cleaned.append("lock"))
        self.stack.register(f"stage{i}", cleanup, depends_on=depends)

    def run(self, interrupt_after):
        """interrupt_after: 在第 N 步完成后注入中断；None 表示不中断。"""
        try:
            for i in range(self.STEPS):
                self.step(i)
                if interrupt_after is not None and i == interrupt_after:
                    raise InjectedInterrupt(f"interrupted after step {i}")
            self.committed = True
        except InjectedInterrupt:
            raise
        finally:
            self.report = self.stack.close()
        return self.report

    def verify_consistent(self, test, interrupted_at):
        """验证：所有已创建资源都被清理，顺序正确，无残留。"""
        n_created = (interrupted_at + 1) if interrupted_at is not None else self.STEPS
        # 1. 每个已创建的阶段资源都执行了清理
        expected_stages = list(range(n_created))
        # 2. 清理顺序：阶段逆序，且 lock 最后（所有 stage 依赖 lock）
        test.assertEqual(self.cleaned[:n_created], expected_stages[::-1])
        test.assertEqual(self.cleaned[-1], "lock")
        # 3. 文件系统状态一致：半成品文件全部删除
        for p in self.created:
            test.assertFalse(os.path.exists(p), f"leftover: {p}")
        # 4. 报告完整：无失败，执行数 = 阶段数 + 1（lock）
        test.assertTrue(self.report.ok, self.report.summary())
        test.assertEqual(len(self.report.executed), n_created + 1)
        # 5. 中断时业务未提交
        test.assertEqual(self.committed, interrupted_at is None)


class TestInterruptAtEveryStage(unittest.TestCase):
    """在流水线的每一步之后注入中断，逐阶段验证清理完整性。"""

    def test_interrupt_injection_all_stages(self):
        for cut in list(range(Pipeline.STEPS)) + [None]:
            with self.subTest(interrupt_after=cut):
                pipe = Pipeline()
                try:
                    try:
                        pipe.run(interrupt_after=cut)
                    except InjectedInterrupt:
                        self.assertIsNotNone(cut, "不中断场景不应抛出")
                    pipe.verify_consistent(self, interrupted_at=cut)
                    # 中断后再次 close（模拟信号重入）：无副作用
                    again = pipe.stack.close()
                    self.assertIs(again, pipe.report)
                finally:
                    shutil.rmtree(pipe.tmpdir, ignore_errors=True)

    def test_interrupt_with_failing_cleanup(self):
        """中断场景 + 某阶段清理抛异常：其余清理仍完整。"""
        pipe = Pipeline()
        try:
            original = pipe.stack.register

            def register_with_bug(name, fn, depends_on=()):
                if name == "stage2":
                    def buggy():
                        raise OSError("cannot remove stage2")
                    original(name, buggy, depends_on)
                else:
                    original(name, fn, depends_on)

            pipe.stack.register = register_with_bug
            try:
                pipe.run(interrupt_after=4)
            except InjectedInterrupt:
                pass
            # stage2 失败被记录，其余全部清理成功
            self.assertFalse(pipe.report.ok)
            self.assertEqual([f.name for f in pipe.report.failures], ["stage2"])
            self.assertEqual(len(pipe.report.executed), 6)  # 5 stage + lock
            remaining = [p for p in pipe.created
                         if os.path.exists(p) and "stage2" not in p]
            self.assertEqual(remaining, [])
        finally:
            shutil.rmtree(pipe.tmpdir, ignore_errors=True)


# ------------------------------------------------------- 信号集成

class TestSignalIntegration(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, "SIGINT"), "需要信号支持")
    def test_sigint_cleanup_runs(self):
        calls = []
        stack = CleanupStack()
        stack.register("res", lambda: calls.append("res"))
        with self.assertRaises(KeyboardInterrupt):
            with signal_cleanup(stack):
                os.kill(os.getpid(), signal.SIGINT)
        self.assertEqual(calls, ["res"])
        self.assertTrue(stack.closed)


# ------------------------------------------------------- 规模与耗时

class TestScaleAndTiming(unittest.TestCase):
    def test_many_resources(self):
        """大量资源：全部清理、顺序正确、记录耗时。"""
        n = 20_000
        counter = []
        stack = CleanupStack()
        for i in range(n):
            stack.register(f"r{i}", lambda i=i: counter.append(i))
        t0 = time.perf_counter()
        report = stack.close()
        dt = time.perf_counter() - t0
        self.assertTrue(report.ok)
        self.assertEqual(len(report.executed), n)
        self.assertEqual(counter, list(range(n))[::-1])  # 严格 LIFO
        print(f"\n[timing] {n} resources cleaned in {dt*1000:.2f} ms "
              f"({dt/n*1e9:.0f} ns/op)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
