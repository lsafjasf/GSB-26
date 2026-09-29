"""复现/对照 BuggyAggregator 的四类现网问题。

- test_1（并发写入）按严格正确性口径断言：同一批次并发投递必须
  exactly-once，且总数严格等于成功批次的样本数之和。用事件把竞态
  窗口钉死在重叠区间，因此该用例在缺陷版上确定性失败（同时暴露
  缺陷幅度）；同样的严格口径在修复版上由 test_aggregator.py 全绿保障。
- test_2~4 断言“缺陷行为确实发生”，在缺陷版上通过、在修复版上失败。

运行：python3 -m unittest test_reproduce -v
"""

import threading
import unittest

from aggregator_buggy import BuggyAggregator


class ReproduceTest(unittest.TestCase):
    def test_1_concurrent_same_batch_counted_twice(self):
        """并发写入（严格口径）：同一批次并发投递必须 exactly-once。

        用事件把竞态窗口钉死在重叠区间：线程 A 通过“未见过”检查后被
        拦在窗口内，线程 B 完整提交同一批次后才放行 A，保证两个线程的
        check-then-act 确定性重叠，结果与线程调度无关。
        """
        agg = BuggyAggregator()
        entered = threading.Event()  # 线程 A 已通过去重检查、进入竞态窗口
        release = threading.Event()  # 线程 B 完整提交后放行线程 A

        def hook():
            # 只拦截第一个进入窗口的线程（A）；其余钩子调用直接放行
            if not entered.is_set():
                entered.set()
                release.wait(5)

        agg.yield_hook = hook

        batch = [("dim_a", 5), ("dim_b", 3)]
        batch_sum = sum(v for _, v in batch)  # 8
        results = []

        def worker():
            results.append(agg.apply_batch("batch-1", batch))

        ta = threading.Thread(target=worker)
        ta.start()
        entered.wait(5)        # 等 A 通过检查并停在竞态窗口内
        tb = threading.Thread(target=worker)
        tb.start()
        tb.join()              # B 完整提交（A 仍被拦在窗口内）
        release.set()          # 放行 A：它早已通过检查，缺陷版会再计一次
        ta.join()

        # 严格口径：同一代次并发投递必须恰好生效一次，
        # 且总数严格等于成功批次的样本数之和。
        self.assertEqual(results.count(True), 1)
        counts, total = agg.snapshot()
        self.assertEqual(total, batch_sum)
        self.assertEqual(counts, {"dim_a": 5, "dim_b": 3})
        self.assertEqual(sum(counts.values()), total)

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
