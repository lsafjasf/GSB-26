"""修复版 Aggregator 的回归测试：自洽、幂等、单调、一致读、all-or-nothing。

运行：python3 -m unittest test_aggregator -v
"""

import threading
import time
import unittest

from aggregator import Aggregator

THREADS = 8
BATCHES_PER_THREAD = 300
DIMS = ["dim_%d" % i for i in range(5)]


def make_batch(seed):
    return [(DIMS[(seed + i) % len(DIMS)], (seed + i) % 7 + 1) for i in range(4)]


class AggregatorTestBase:
    AGG_CLS = None

    def test_self_consistency_under_concurrency(self):
        """统计总数严格等于所有成功批次样本数之和。"""
        agg = self.AGG_CLS()
        expected = [0]
        lock = threading.Lock()

        def worker(tid):
            local = 0
            for i in range(BATCHES_PER_THREAD):
                batch = make_batch(tid * BATCHES_PER_THREAD + i)
                self.assertTrue(agg.apply_batch("t%d-b%d" % (tid, i), batch))
                local += sum(v for _, v in batch)
            with lock:
                expected[0] += local

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        counts, total = agg.snapshot()
        self.assertEqual(total, expected[0])                 # 总数严格相等
        self.assertEqual(sum(counts.values()), total)        # 维度之和 == 总数
        self.assertTrue(all(v >= 0 for v in counts.values()))

    def test_idempotent_redelivery(self):
        """同一批次并发 + 顺序重复投递，只生效一次。"""
        agg = self.AGG_CLS()
        batch = [("dim_a", 5), ("dim_b", 3)]
        results = []

        def worker():
            results.append(agg.apply_batch("batch-1", batch))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 顺序重试同样幂等
        self.assertFalse(agg.apply_batch("batch-1", batch))
        self.assertFalse(agg.apply_batch("batch-1", batch))

        self.assertEqual(results.count(True), 1)             # 恰好生效一次
        self.assertEqual(results.count(False), 15)
        counts, total = agg.snapshot()
        self.assertEqual(total, 8)
        self.assertEqual(counts, {"dim_a": 5, "dim_b": 3})

    def test_failed_batch_is_all_or_nothing_and_retryable(self):
        """失败批次零改动；修正后重试恰好计入一次。"""
        agg = self.AGG_CLS()
        agg.apply_batch("ok", [("dim_a", 1)])
        before = agg.snapshot()

        with self.assertRaises(ValueError):
            agg.apply_batch("bad", [("dim_b", 4), ("dim_c", -1)])
        self.assertEqual(agg.snapshot(), before)             # 状态零改动

        self.assertTrue(agg.apply_batch("bad", [("dim_b", 4), ("dim_c", 2)]))
        counts, total = agg.snapshot()
        self.assertEqual(total, 1 + 6)
        self.assertEqual(counts, {"dim_a": 1, "dim_b": 4, "dim_c": 2})

    def test_concurrent_reads_always_consistent_and_monotonic(self):
        """边写边读：每次快照都是一致状态，且总量单调不回退、无负数。"""
        agg = self.AGG_CLS()
        stop = threading.Event()
        violations = []

        def writer(tid):
            i = 0
            while not stop.is_set():
                agg.apply_batch("t%d-b%d" % (tid, i), make_batch(i))
                i += 1

        def reader():
            prev_total = 0
            while not stop.is_set():
                counts, total = agg.snapshot()
                if sum(counts.values()) != total:
                    violations.append(("inconsistent", counts, total))
                if total < prev_total:
                    violations.append(("rollback", prev_total, total))
                if any(v < 0 for v in counts.values()):
                    violations.append(("negative", counts, total))
                prev_total = total

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers + writers:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in readers + writers:
            t.join()

        self.assertEqual(violations, [])
        counts, total = agg.snapshot()
        self.assertEqual(sum(counts.values()), total)

    def test_invalid_input_rejected(self):
        agg = self.AGG_CLS()
        for bad in ([("d", -1)], [("d", 1.5)], [("d", True)], [("", 1)], [(None, 1)]):
            with self.assertRaises(ValueError):
                agg.apply_batch("b", bad)
        with self.assertRaises(ValueError):
            agg.apply_batch(None, [("d", 1)])
        self.assertEqual(agg.snapshot(), ({}, 0))


class AggregatorTest(AggregatorTestBase, unittest.TestCase):
    AGG_CLS = Aggregator


if __name__ == "__main__":
    unittest.main()
