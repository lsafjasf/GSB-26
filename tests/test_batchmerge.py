"""批处理 / 合并库自测：虚拟时钟驱动，完全确定性。

运行：
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batchmerge import (
    BatchError,
    Batcher,
    BatchTimeout,
    RealScheduler,
    RequestCancelled,
    VirtualClock,
)


def make_handler(calls, fail_keys=(), raise_exc=None, delay_ms=None, clock=None):
    """构造测试用 handler。

    calls: list，每次下游调用 append 一个键列表（用于计数断言）。
    fail_keys: 其中的键以子请求失败返回。
    raise_exc: 若设置，handler 整体抛出该异常（整批失败）。
    delay_ms/clock: 设置后 handler 异步，在 delay_ms 后 complete。
    """

    def handler(keys, complete):
        calls.append(list(keys))
        if delay_ms is None:
            result = {}
            for key in keys:
                result[key] = RuntimeError(f"sub failure: {key}") if key in fail_keys else f"v:{key}"
            if raise_exc is not None:
                raise raise_exc
            return result
        # 异步路径：安排稍后 complete
        def fire():
            if raise_exc is not None:
                complete(raise_exc)
                return
            result = {}
            for key in keys:
                result[key] = RuntimeError(f"sub failure: {key}") if key in fail_keys else f"v:{key}"
            complete(result)

        clock.schedule(delay_ms, fire)
        return None

    return handler


class BatchMergeTests(unittest.TestCase):
    def test_single_request_within_window(self):
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=10, window_ms=50, scheduler=clock)

        f = b.load("a")
        self.assertFalse(f.done())
        self.assertEqual(calls, [])  # 窗口未到，尚未提交

        clock.advance(49)
        self.assertFalse(f.done())
        clock.advance(1)  # 到达窗口边界
        self.assertEqual(f.result(), "v:a")
        self.assertEqual(calls, [["a"]])

    def test_window_boundary_simultaneous_arrivals(self):
        # 窗口边界同一时刻到达的多个不同键，必须合并为一次下游调用。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=100, window_ms=10, scheduler=clock)

        futures = [b.load(f"k{i}") for i in range(50)]
        clock.advance(10)  # 全部在窗口结束时一起触发
        values = [f.result() for f in futures]
        self.assertEqual(values, [f"v:k{i}" for i in range(50)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            sorted(calls[0], key=lambda x: int(x[1:])),
            [f"k{i}" for i in range(50)],
        )

    def test_concurrent_same_key_merged_with_count_assertion(self):
        # 同一键的并发请求必须合并：100 个 load 只产生 1 次下游调用。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=1000, window_ms=20, scheduler=clock)

        futures = [b.load("hot") for _ in range(100)]
        clock.advance(20)
        for f in futures:
            self.assertEqual(f.result(), "v:hot")
        self.assertEqual(len(calls), 1, f"同键应合并为 1 次调用，实际 {len(calls)}")
        self.assertEqual(calls, [["hot"]])

    def test_inflight_same_key_merged(self):
        # 已提交但尚未返回（在途）的同键请求也必须合并。
        clock = VirtualClock()
        calls = []
        b = Batcher(
            make_handler(calls, delay_ms=100, clock=clock),
            max_batch_size=2,
            window_ms=10,
            timeout_ms=1000,
            scheduler=clock,
        )

        first = b.load("x")
        clock.advance(10)  # 提交，开始在途
        self.assertEqual(calls, [["x"]])
        followers = [b.load("x") for _ in range(5)]  # 在途期间到达
        clock.advance(100)  # 下游返回
        self.assertEqual(first.result(), "v:x")
        for f in followers:
            self.assertEqual(f.result(), "v:x")
        self.assertEqual(len(calls), 1, "在途同键也应合并，不能再次打下游")

    def test_size_threshold_triggers_early(self):
        # 数量阈值先到：无需等窗口结束立即提交。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=3, window_ms=100, scheduler=clock)

        fs = [b.load(k) for k in ("a", "b", "c")]
        self.assertEqual(calls, [["a", "b", "c"]])  # 立即提交
        clock.advance(0)
        self.assertEqual([f.result() for f in fs], ["v:a", "v:b", "v:c"])

    def test_batch_over_limit_is_sharded(self):
        # flush / 定时器触发时若键数超过上限，必须分片提交。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=3, window_ms=50, scheduler=clock)

        keys = [f"k{i}" for i in range(10)]
        fs = [b.load(k) for k in keys]
        clock.advance(50)
        for f, k in zip(fs, keys):
            self.assertEqual(f.result(), f"v:{k}")
        shard_sizes = sorted(len(c) for c in calls)
        self.assertEqual(shard_sizes, [1, 3, 3, 3])  # 10 = 3+3+3+1
        self.assertEqual(sum(map(len, calls)), 10)
        flat = [k for c in calls for k in c]
        self.assertEqual(sorted(flat), sorted(keys))

    def test_threshold_flush_shards_remainder(self):
        # 阈值触发提交时，超出上限的缓冲也应在同一轮全部分片提交。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=2, window_ms=100, scheduler=clock)

        for k in ("a", "b", "c", "d", "e"):
            b.load(k)
        # 第 2 个 load 即触发；到第 5 个时已发生多次阈值提交
        self.assertEqual(sorted(len(c) for c in calls), [2, 2])
        clock.advance(100)  # 余数 e 随窗口提交
        self.assertEqual(sorted(len(c) for c in calls), [1, 2, 2])

    def test_sub_request_failure_isolated(self):
        # 某个子请求失败不影响其他子请求，失败原因回传给对应调用方。
        clock = VirtualClock()
        calls = []
        b = Batcher(
            make_handler(calls, fail_keys={"bad"}),
            max_batch_size=10,
            window_ms=10,
            scheduler=clock,
        )

        ok1 = b.load("a")
        bad = b.load("bad")
        ok2 = b.load("b")
        clock.advance(10)
        self.assertEqual(ok1.result(), "v:a")
        self.assertEqual(ok2.result(), "v:b")
        with self.assertRaisesRegex(RuntimeError, "sub failure: bad"):
            bad.result()

    def test_overall_batch_failure_fans_out(self):
        # 整批失败：该批所有等待方都收到 BatchError（可区分），无人挂起。
        clock = VirtualClock()
        calls = []
        boom = ConnectionError("downstream exploded")
        b = Batcher(
            make_handler(calls, raise_exc=boom),
            max_batch_size=10,
            window_ms=10,
            scheduler=clock,
        )

        fs = [b.load(k) for k in ("a", "b", "c")]
        clock.advance(10)
        for f in fs:
            with self.assertRaises(BatchError) as cm:
                f.result(timeout=1)
            self.assertIs(cm.exception.__cause__, boom)

    def test_timeout_all_waiters_fail_distinguishably(self):
        # 整批超时：所有等待方收到 BatchTimeout（区别于 BatchError），不挂起。
        clock = VirtualClock()
        calls = []
        b = Batcher(
            make_handler(calls, delay_ms=1000, clock=clock),  # 1000ms 才返回
            max_batch_size=10,
            window_ms=10,
            timeout_ms=100,
            scheduler=clock,
        )

        fs = [b.load(k) for k in ("a", "b")]
        clock.advance(10)   # 提交
        clock.advance(100)  # 超时
        for f in fs:
            self.assertTrue(f.done(), "超时后不得有调用方挂起")
            with self.assertRaises(BatchTimeout):
                f.result(timeout=1)

        # 迟到的下游响应不得复活/覆盖已超时的结果
        clock.advance(1000)
        for f in fs:
            with self.assertRaises(BatchTimeout):
                f.result()

    def test_late_response_does_not_hang_or_override(self):
        # 无超时配置，但响应最终到达：正常拿到结果（对照用，确保不是一律超时）。
        clock = VirtualClock()
        calls = []
        b = Batcher(
            make_handler(calls, delay_ms=300, clock=clock),
            max_batch_size=5,
            window_ms=10,
            timeout_ms=None,
            scheduler=clock,
        )
        f = b.load("z")
        clock.advance(10)
        clock.advance(300)
        self.assertEqual(f.result(), "v:z")

    def test_cancel_before_dispatch_removes_request(self):
        # 提交前取消：不产生该键的下游调用，调用方收到 RequestCancelled。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=10, window_ms=20, scheduler=clock)

        f1 = b.load("a")
        f2 = b.load("keep")
        self.assertTrue(f1.cancel())
        clock.advance(20)
        with self.assertRaises(RequestCancelled):
            f1.result()
        self.assertEqual(f2.result(), "v:keep")
        self.assertEqual(calls, [["keep"]])  # 被取消的键未下发

    def test_cancel_one_merged_caller_keeps_others(self):
        # 同键合并后，其中一个调用方取消，其他调用方仍正常拿到结果。
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=100, window_ms=20, scheduler=clock)

        f1 = b.load("hot")
        f2 = b.load("hot")
        f3 = b.load("hot")
        self.assertTrue(f2.cancel())
        clock.advance(20)
        with self.assertRaises(RequestCancelled):
            f2.result()
        self.assertEqual(f1.result(), "v:hot")
        self.assertEqual(f3.result(), "v:hot")
        self.assertEqual(calls, [["hot"]])  # 仍有等待者，照常一次下游调用

    def test_cancel_all_merged_callers_skips_dispatch(self):
        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=100, window_ms=20, scheduler=clock)

        fs = [b.load("hot") for _ in range(3)]
        for f in fs:
            self.assertTrue(f.cancel())
        clock.advance(20)
        self.assertEqual(calls, [])  # 无人等待 => 不提交
        for f in fs:
            with self.assertRaises(RequestCancelled):
                f.result()

    def test_cancel_after_dispatch_detaches_only_caller(self):
        # 提交后取消：下游调用照常，其余等待方拿结果，取消方收到取消。
        clock = VirtualClock()
        calls = []
        b = Batcher(
            make_handler(calls, delay_ms=100, clock=clock),
            max_batch_size=2,
            window_ms=10,
            timeout_ms=1000,
            scheduler=clock,
        )
        f1 = b.load("a")
        f2 = b.load("a")
        clock.advance(10)  # 已提交、在途
        self.assertTrue(f1.cancel())
        clock.advance(100)
        with self.assertRaises(RequestCancelled):
            f1.result()
        self.assertEqual(f2.result(), "v:a")
        self.assertEqual(len(calls), 1)

    def test_missing_key_treated_as_batch_failure(self):
        # 下游漏掉某个键：整批失败，避免对应调用方永远挂起。
        clock = VirtualClock()

        def handler(keys, complete):
            complete({k: f"v:{k}" for k in keys if k != "lost"})

        b = Batcher(handler, max_batch_size=10, window_ms=10, scheduler=clock)
        fs = {k: b.load(k) for k in ("a", "lost", "b")}
        clock.advance(10)
        for f in fs.values():
            with self.assertRaises(BatchError):
                f.result()

    def test_call_counts_before_vs_after_merging(self):
        # 调用次数对比数据：同样 2000 个请求，80 个键各 25 次并发。
        # 朴素直连：2000 次下游调用；开启合并：去重后 80 个键一次窗口 => 1 次。
        n_unique = 80
        repeats_per_key = 25
        naive_calls = n_unique * repeats_per_key  # 2000

        clock = VirtualClock()
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=100, window_ms=10, scheduler=clock)

        futures = [
            b.load(k)
            for _ in range(repeats_per_key)
            for k in range(n_unique)
        ]
        self.assertEqual(calls, [])  # 窗口内尚未提交
        clock.advance(10)
        for f in futures:
            self.assertTrue(f.done())
        merged_calls = len(calls)
        self.assertEqual(sum(map(len, calls)), n_unique)  # 同键合并，去重后 80

        reduction = 1.0 - merged_calls / naive_calls
        self.assertLess(merged_calls, naive_calls)
        self.assertEqual(merged_calls, 1)  # 80 个键 <= 单批上限 100
        self.assertAlmostEqual(reduction, 0.9995, places=4)  # 下降 99.95%

    def test_real_scheduler_smoke(self):
        # 真实时钟 + 真实线程的端到端冒烟（轻量、带超时保护）。
        calls = []
        b = Batcher(make_handler(calls), max_batch_size=100, window_ms=50, scheduler=RealScheduler())
        f1, f2 = b.load("a"), b.load("a")
        self.assertEqual(f1.result(timeout=2), "v:a")
        self.assertEqual(f2.result(timeout=2), "v:a")
        self.assertEqual(len(calls), 1)
        b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
