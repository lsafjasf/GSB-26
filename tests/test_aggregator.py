"""修复后 Aggregator 的自洽性、幂等性、一致性与原子性回归测试。"""

import random
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aggregator import Aggregator

THREADS = 8
BATCHES_PER_THREAD = 500
BATCH_SIZE = 6
DIMS = ["dimA", "dimB", "dimC", "dimD"]


def make_batch(tid, seq):
    rng = random.Random(tid * 1_000_000 + seq)
    return [(rng.choice(DIMS), rng.randint(0, 5)) for _ in range(BATCH_SIZE)]


class TestAggregator(unittest.TestCase):
    def test_concurrent_writes_exact_total(self):
        """自洽断言：统计总数严格等于成功批次样本数之和。"""
        agg = Aggregator()
        expected = 0
        expected_lock = threading.Lock()
        barrier = threading.Barrier(THREADS)

        def worker(tid):
            nonlocal expected
            local = 0
            barrier.wait()
            for seq in range(BATCHES_PER_THREAD):
                samples = make_batch(tid, seq)
                assert agg.apply_batch(f"t{tid}-b{seq}", samples) is True
                local += sum(n for _, n in samples)
            with expected_lock:
                expected += local

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = agg.snapshot()
        self.assertEqual(snap["cum_total"], expected)
        self.assertEqual(snap["total"], expected)
        self.assertEqual(sum(snap["dims"].values()), snap["total"])
        self.assertEqual(sum(snap["cum_dims"].values()), snap["cum_total"])

    def test_batch_retry_idempotent(self):
        """幂等断言：同一 batch_id 重复投递只计一次。"""
        agg = Aggregator()
        samples = [("dimA", 3), ("dimB", 4)]
        self.assertTrue(agg.apply_batch("batch-1", samples))
        for _ in range(5):
            self.assertFalse(agg.apply_batch("batch-1", samples))
        snap = agg.snapshot()
        self.assertEqual(snap["cum_total"], 7)
        self.assertEqual(snap["dims"], {"dimA": 3, "dimB": 4})

    def test_concurrent_retry_storm_idempotent(self):
        """多线程同时重投同一批次，仍然只计一次。"""
        agg = Aggregator()
        samples = [("dimA", 2), ("dimC", 5)]
        barrier = threading.Barrier(THREADS)

        def worker():
            barrier.wait()
            for _ in range(200):
                agg.apply_batch("hot-batch", samples)

        threads = [threading.Thread(target=worker) for _ in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(agg.snapshot()["cum_total"], 7)

    def test_snapshot_consistent_under_concurrency(self):
        """并发读断言：任何快照都是某个一致状态，且累计值单调不回退。"""
        agg = Aggregator()
        done = threading.Event()
        violations = []

        def writer(tid):
            for seq in range(BATCHES_PER_THREAD):
                agg.apply_batch(f"t{tid}-b{seq}", make_batch(tid, seq))

        def reader():
            prev_cum = 0
            while not done.is_set():
                snap = agg.snapshot()
                ok = (
                    sum(snap["dims"].values()) == snap["total"]
                    and sum(snap["cum_dims"].values()) == snap["cum_total"]
                    and snap["cum_total"] >= prev_cum
                    and all(v >= 0 for v in snap["dims"].values())
                    and all(v >= 0 for v in snap["cum_dims"].values())
                )
                if not ok:
                    violations.append(snap)
                prev_cum = snap["cum_total"]

        writers = [threading.Thread(target=writer, args=(i,)) for i in range(THREADS)]
        rt = threading.Thread(target=reader)
        rt.start()
        for t in writers:
            t.start()
        for t in writers:
            t.join()
        done.set()
        rt.join()

        self.assertEqual(violations, [])
        expected = sum(
            sum(n for _, n in make_batch(t, s))
            for t in range(THREADS)
            for s in range(BATCHES_PER_THREAD)
        )
        self.assertEqual(agg.snapshot()["cum_total"], expected)

    def test_failed_batch_is_atomic(self):
        """原子性断言：失败批次零副作用，修正后同 batch_id 可安全重投。"""
        agg = Aggregator()
        bad = [("dimA", 5), ("dimB", -1)]
        with self.assertRaises(ValueError):
            agg.apply_batch("batch-x", bad)
        snap = agg.snapshot()
        self.assertEqual(snap["cum_total"], 0)
        self.assertEqual(snap["dims"], {})

        fixed = [("dimA", 5), ("dimB", 2)]
        self.assertTrue(agg.apply_batch("batch-x", fixed))
        self.assertFalse(agg.apply_batch("batch-x", fixed))
        self.assertEqual(agg.snapshot()["cum_total"], 7)

    def test_flush_monotonic_no_negative_no_regression(self):
        """边写边 flush：窗口增量非负、累计值单调、增量之和等于累计。"""
        agg = Aggregator()
        done = threading.Event()
        deltas = []
        violations = []

        def writer(tid):
            for seq in range(200):
                agg.apply_batch(f"t{tid}-b{seq}", make_batch(tid, seq))

        def flusher():
            prev_cum = 0
            while not done.is_set():
                window = agg.flush()
                snap = agg.snapshot()
                if window["total"] < 0 or any(v < 0 for v in window["dims"].values()):
                    violations.append(window)
                if snap["cum_total"] < prev_cum:
                    violations.append(snap)
                prev_cum = snap["cum_total"]
                deltas.append(window["total"])

        writers = [threading.Thread(target=writer, args=(i,)) for i in range(THREADS)]
        ft = threading.Thread(target=flusher)
        ft.start()
        for t in writers:
            t.start()
        for t in writers:
            t.join()
        done.set()
        ft.join()
        deltas.append(agg.flush()["total"])

        self.assertEqual(violations, [])
        self.assertEqual(sum(deltas), agg.snapshot()["cum_total"])
        self.assertEqual(agg.snapshot()["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
