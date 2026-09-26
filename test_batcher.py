"""batcher 库自测：分发正确性、合并计数、超时、边界、分片、取消、调用次数对比。

运行：python3 -m unittest test_batcher -v
"""

import heapq
import threading
import time
import unittest
from concurrent.futures import CancelledError

from batcher import Batcher, BatchResultError, BatchTimeoutError


class ManualScheduler:
    """虚拟时钟调度器：时间只能由测试显式推进，保证边界场景可复现。"""

    class _Handle:
        def __init__(self, callback):
            self.callback = callback
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    def __init__(self):
        self.now = 0.0
        self._seq = 0
        self._queue = []  # (deadline, seq, handle)

    def call_later(self, delay, callback):
        self._seq += 1
        handle = ManualScheduler._Handle(callback)
        heapq.heappush(self._queue, (self.now + delay, self._seq, handle))
        return handle

    def advance(self, delta):
        target = self.now + delta
        while self._queue and self._queue[0][0] <= target:
            deadline, _, handle = heapq.heappop(self._queue)
            if handle.cancelled:
                continue
            self.now = deadline
            handle.callback()
        self.now = target


class RecordingHandler:
    """记录每次下游调用，并按脚本返回结果。"""

    def __init__(self, fn=None):
        self.calls = []  # 每次调用的请求列表
        self.fn = fn or (lambda reqs: [("ok", r) for r in reqs])
        self.lock = threading.Lock()

    def __call__(self, requests):
        with self.lock:
            self.calls.append(list(requests))
        return self.fn(requests)

    @property
    def n_calls(self):
        with self.lock:
            return len(self.calls)


def make_batcher(handler, max_batch_size=100, window=1.0, timeout=None):
    scheduler = ManualScheduler()
    batcher = Batcher(handler, max_batch_size=max_batch_size,
                      window=window, timeout=timeout, scheduler=scheduler)
    return batcher, scheduler


class TriggerTest(unittest.TestCase):
    """数量阈值与时间窗口，先到先触发。"""

    def test_count_threshold_triggers_before_window(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=3, window=10.0)
        futs = [b.submit(f"k{i}", i) for i in range(3)]
        # 未到窗口时间，但数量已满 => 已触发
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(handler.calls[0], [0, 1, 2])
        self.assertEqual([f.result(timeout=5) for f in futs],
                         [("ok", 0), ("ok", 1), ("ok", 2)])

    def test_window_triggers_before_count(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f = b.submit("k", "req")
        self.assertEqual(handler.n_calls, 0)  # 窗口未到期不触发
        sch.advance(1.0)
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(f.result(timeout=5), ("ok", "req"))

    def test_single_request_within_window(self):
        """窗口内只有一个请求：窗口到期后正常提交并拿到结果。"""
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=50, window=0.5)
        f = b.submit("only", {"x": 1})
        sch.advance(0.4)
        self.assertEqual(handler.n_calls, 0)
        sch.advance(0.1)  # 恰好到窗口边界
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(handler.calls[0], [{"x": 1}])
        self.assertEqual(f.result(timeout=5), ("ok", {"x": 1}))


class MergeTest(unittest.TestCase):
    """同键并发请求合并，用计数断言下游调用次数。"""

    def test_same_key_merges_into_one_downstream_item(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        futs = [b.submit("same-key", f"payload-{i}") for i in range(5)]
        sch.advance(1.0)
        # 5 个调用方 => 下游只收到 1 条请求、只调用 1 次
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(len(handler.calls[0]), 1)
        results = [f.result(timeout=5) for f in futs]
        self.assertEqual(results, [results[0]] * 5)  # 共享同一结果
        stats = b.stats
        self.assertEqual(stats["submitted"], 5)
        self.assertEqual(stats["merged"], 4)
        self.assertEqual(stats["downstream_calls"], 1)
        self.assertEqual(stats["downstream_items"], 1)

    def test_distinct_keys_not_merged(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        futs = [b.submit(f"key-{i}", i) for i in range(4)]
        sch.advance(1.0)
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(len(handler.calls[0]), 4)
        self.assertEqual([f.result(timeout=5) for f in futs],
                         [("ok", i) for i in range(4)])


class DispatchTest(unittest.TestCase):
    """批量结果逐条分发：单条失败不影响其他条目。"""

    def test_partial_failure_isolated(self):
        boom = ValueError("item-2 boom")

        def fn(reqs):
            out = []
            for r in reqs:
                out.append(boom if r == "bad" else ("ok", r))
            return out

        handler = RecordingHandler(fn)
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f_ok1 = b.submit("a", "good-1")
        f_bad = b.submit("b", "bad")
        f_ok2 = b.submit("c", "good-2")
        sch.advance(1.0)

        self.assertEqual(f_ok1.result(timeout=5), ("ok", "good-1"))
        self.assertEqual(f_ok2.result(timeout=5), ("ok", "good-2"))
        with self.assertRaises(ValueError) as ctx:
            f_bad.result(timeout=5)
        self.assertIs(ctx.exception, boom)  # 失败原因原样回传给对应调用方

    def test_overall_failure_broadcast_to_all(self):
        class DownstreamDown(Exception):
            pass

        error = DownstreamDown("connection reset")

        def fn(reqs):
            raise error

        handler = RecordingHandler(fn)
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        futs = [b.submit(f"k{i}", i) for i in range(4)]
        sch.advance(1.0)
        for f in futs:
            with self.assertRaises(DownstreamDown) as ctx:
                f.result(timeout=5)
            self.assertIs(ctx.exception, error)
            # 与超时错误可区分
            self.assertNotIsInstance(ctx.exception, BatchTimeoutError)

    def test_malformed_handler_result_fails_all(self):
        handler = RecordingHandler(lambda reqs: ["too-few"])
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        futs = [b.submit(f"k{i}", i) for i in range(3)]
        sch.advance(1.0)
        for f in futs:
            with self.assertRaises(BatchResultError):
                f.result(timeout=5)


class TimeoutTest(unittest.TestCase):
    """批量提交超时：所有等待方收到 BatchTimeoutError，无人永远挂起。"""

    def test_timeout_broadcast_and_distinguishable(self):
        started = threading.Event()

        def slow_fn(reqs):
            started.set()
            time.sleep(2.0)  # 远超 timeout
            return [("ok", r) for r in reqs]

        handler = RecordingHandler(slow_fn)
        b, _ = make_batcher(handler, max_batch_size=2, window=60.0, timeout=0.1)
        futs = [b.submit(f"k{i}", i) for i in range(2)]  # 数量阈值立即触发
        self.assertTrue(started.wait(timeout=5))
        for f in futs:
            with self.assertRaises(BatchTimeoutError) as ctx:
                f.result(timeout=5)
            self.assertIsInstance(ctx.exception, TimeoutError)  # 可区分类型
        # 所有 future 都已结算，无人挂起
        self.assertTrue(all(f.done() for f in futs))

    def test_timeout_error_differs_from_overall_failure(self):
        self.assertTrue(issubclass(BatchTimeoutError, TimeoutError))
        self.assertNotEqual(BatchTimeoutError, Exception)


class BoundaryTest(unittest.TestCase):
    """窗口边界同时到达：边界前的请求与边界后的请求落在不同批次。"""

    def test_arrival_exactly_at_boundary_starts_new_batch(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f_a = b.submit("a", "A")          # t=0，开启窗口 [0, 1]
        sch.advance(1.0)                  # t=1：定时器触发，A 落第一批
        f_b = b.submit("b", "B")          # t=1：边界同时到达，落新窗口
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(handler.calls[0], ["A"])
        sch.advance(1.0)                  # t=2：第二个窗口到期
        self.assertEqual(handler.n_calls, 2)
        self.assertEqual(handler.calls[1], ["B"])
        self.assertEqual(f_a.result(timeout=5), ("ok", "A"))
        self.assertEqual(f_b.result(timeout=5), ("ok", "B"))

    def test_arrival_just_before_boundary_joins_current_batch(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f_a = b.submit("a", "A")          # t=0
        sch.advance(0.999)                # t=0.999，窗口未到期
        f_b = b.submit("b", "B")          # 边界前到达 => 同批
        sch.advance(0.001)                # t=1：窗口到期
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(handler.calls[0], ["A", "B"])
        self.assertEqual(f_a.result(timeout=5), ("ok", "A"))
        self.assertEqual(f_b.result(timeout=5), ("ok", "B"))


class ShardTest(unittest.TestCase):
    """超过数量上限的请求必须分片提交。"""

    def test_overflow_is_sharded(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=3, window=10.0)
        futs = [b.submit(f"k{i}", i) for i in range(7)]
        # 7 条 => 3 + 3 已立即触发，剩 1 条等窗口
        self.assertEqual(handler.n_calls, 2)
        self.assertEqual(handler.calls[0], [0, 1, 2])
        self.assertEqual(handler.calls[1], [3, 4, 5])
        sch.advance(10.0)
        self.assertEqual(handler.n_calls, 3)
        self.assertEqual(handler.calls[2], [6])
        self.assertEqual([f.result(timeout=5) for f in futs],
                         [("ok", i) for i in range(7)])
        self.assertEqual(b.stats["downstream_items"], 7)


class CancelTest(unittest.TestCase):
    """请求被取消：未提交的不下行；合并场景不影响其他调用方。"""

    def test_cancel_before_flush_prevents_downstream_call(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f = b.submit("k", "req")
        self.assertTrue(f.cancel())
        sch.advance(1.0)
        self.assertEqual(handler.n_calls, 0)  # 没有等待方 => 不下行
        with self.assertRaises(CancelledError):
            f.result(timeout=5)
        self.assertEqual(b.stats["cancelled"], 1)

    def test_cancel_one_of_merged_waiters_keeps_others(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f1 = b.submit("same", "req")
        f2 = b.submit("same", "req")
        f3 = b.submit("same", "req")
        self.assertTrue(f2.cancel())
        sch.advance(1.0)
        # 仍有人等待 => 正常下行一次
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(len(handler.calls[0]), 1)
        self.assertEqual(f1.result(timeout=5), ("ok", "req"))
        self.assertEqual(f3.result(timeout=5), ("ok", "req"))
        with self.assertRaises(CancelledError):
            f2.result(timeout=5)

    def test_cancel_one_of_batch_keeps_other_items(self):
        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=100, window=1.0)
        f1 = b.submit("a", "A")
        f2 = b.submit("b", "B")
        self.assertTrue(f1.cancel())
        sch.advance(1.0)
        self.assertEqual(handler.n_calls, 1)
        self.assertEqual(handler.calls[0], ["B"])  # 只剩 B
        self.assertEqual(f2.result(timeout=5), ("ok", "B"))


class CallCountComparisonTest(unittest.TestCase):
    """合并前后处理同样请求量时对下游的调用次数对比。"""

    def test_call_count_comparison(self):
        n_callers = 1000
        n_keys = 10  # 每个 key 被 100 个调用方并发请求

        # 不合并：每个请求直接打下游
        unbatched_calls = n_callers

        handler = RecordingHandler()
        b, sch = make_batcher(handler, max_batch_size=1000, window=0.05)
        futs = [b.submit(f"key-{i % n_keys}", f"req-{i}") for i in range(n_callers)]
        sch.advance(0.05)
        for f in futs:
            f.result(timeout=5)

        stats = b.stats
        batched_calls = stats["downstream_calls"]
        print("\n==== 调用次数对比 ====")
        print(f"逻辑请求量:            {n_callers}")
        print(f"不合并（直连）下游调用: {unbatched_calls}")
        print(f"合并后下游调用:        {batched_calls}")
        print(f"同键合并掉:            {stats['merged']}")
        print(f"下行条目数:            {stats['downstream_items']}")
        print(f"调用次数降低:          "
              f"{(1 - batched_calls / unbatched_calls) * 100:.1f}%")

        self.assertEqual(stats["submitted"], n_callers)
        self.assertEqual(stats["downstream_items"], n_keys)
        self.assertEqual(batched_calls, 1)  # 10 个 key 合并进 1 批
        self.assertLess(batched_calls, unbatched_calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
