"""复现 BuggyAggregator 的四类现网问题。

这些测试断言“缺陷行为确实发生”，因此在缺陷版上全部通过、
在修复版 Aggregator 上会全部失败——这正是回归对照。
运行：python3 -m unittest test_reproduce -v
"""

import threading
import unittest

from aggregator_buggy import BuggyAggregator


class ReproduceTest(unittest.TestCase):
    def test_1_concurrent_same_batch_counted_twice(self):
        """并发写入：两个线程同时投递同一批次，check-then-act 竞态导致计入两次。"""
        agg = BuggyAggregator()
        barrier = threading.Barrier(2)
        agg.yield_hook = barrier.wait  # 两个线程都通过“未见过”检查后在此汇合

        batch = [("dim_a", 5), ("dim_b", 3)]
        results = []

        def worker():
            results.append(agg.apply_batch("batch-1", batch))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 缺陷表现：两次投递都被接受，样本被重复计入（期望总量 8，实际 > 8）
        self.assertEqual(results, [True, True])
        self.assertGreater(agg.total, 8)

    def test_2_retry_after_failure_double_counts(self):
        """批次重试：中途失败后批次标识未登记，重试把已计入样本再计一次。"""
        agg = BuggyAggregator()
        # 第一批：第 2 个样本非法，但第 1 个样本已计入
        with self.assertRaises(ValueError):
            agg.apply_batch("batch-1", [("dim_a", 5), ("dim_b", -1)])
        self.assertEqual(agg.counts["dim_a"], 5)  # 部分样本已残留

        # 修复数据后重试同一批次
        agg.apply_batch("batch-1", [("dim_a", 5), ("dim_b", 3)])

        # 缺陷表现：dim_a 被计入两次（5 + 5），总数 13 而非 8
        self.assertEqual(agg.counts["dim_a"], 10)
        self.assertEqual(agg.total, 13)

    def test_3_snapshot_sees_partial_update(self):
        """边写边读：snapshot 读到 counts 已更新、total 未更新的中间态。"""
        agg = BuggyAggregator()
        counts_updated = threading.Event()
        reader_done = threading.Event()

        calls = [0]

        def hook():
            # 第 2 次调用处于“counts 已改、total 未改”的窗口（第 1 次在循环前）
            calls[0] += 1
            if calls[0] == 2:
                counts_updated.set()
                reader_done.wait(5)

        agg.yield_hook = hook
        observed = {}

        def writer():
            agg.apply_batch("batch-1", [("dim_a", 7), ("dim_b", 1)])

        def reader():
            counts_updated.wait(5)
            counts, total = agg.snapshot()
            observed["counts"] = counts
            observed["total"] = total
            reader_done.set()

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join()
        rt.join()

        # 缺陷表现：sum(counts) != total，读到不一致的中间态
        self.assertEqual(sum(observed["counts"].values()), 7)
        self.assertEqual(observed["total"], 0)
        self.assertNotEqual(sum(observed["counts"].values()), observed["total"])

    def test_4_failed_batch_leaves_partial_state(self):
        """中途失败：部分样本已计入但批次未上报，状态不可回滚且对外可见。"""
        agg = BuggyAggregator()
        with self.assertRaises(ValueError):
            agg.apply_batch("batch-1", [("dim_a", 4), ("dim_b", 6), ("dim_c", -1)])

        counts, total = agg.snapshot()
        # 缺陷表现：失败批次的“前半截”已经留在统计里
        self.assertEqual(counts, {"dim_a": 4, "dim_b": 6})
        self.assertEqual(total, 10)
        # 且批次未登记，调用方无法区分“已部分计入”与“未计入”
        self.assertNotIn("batch-1", agg._seen)


if __name__ == "__main__":
    unittest.main()
