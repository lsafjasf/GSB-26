"""复现现网四类缺陷的回归测试（针对 BuggyAggregator）。

每个用例断言“缺陷确实存在”，即观察到错误行为才算通过；
修复后的 Aggregator 在 tests/test_aggregator.py 中以相反断言验证。
"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aggregator_buggy import BuggyAggregator


def run_threads(fn, n):
    barrier = threading.Barrier(n)

    def wrapper():
        barrier.wait()
        fn()

    threads = [threading.Thread(target=wrapper) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


class TestReproduceBugs(unittest.TestCase):
    def test_1_concurrent_write_double_counted(self):
        """缺陷1：并发写入同一样本，check-then-act 无锁导致计入两次。"""
        n_threads = 8

        class SyncedBuggy(BuggyAggregator):
            """在 check 与 add 之间插入屏障，确定性复现 check-then-act 竞态。"""

            def __init__(self, parties):
                super().__init__()
                self._gate = threading.Barrier(parties)

            def _pause(self):
                self._gate.wait()

        agg = SyncedBuggy(n_threads)
        results = []
        lock = threading.Lock()

        def worker():
            accepted = agg.add_sample("sample-001", "dimA", 1)
            with lock:
                results.append(accepted)

        run_threads(worker, n_threads)
        # 唯一样本数 = 1，但多个线程都通过了去重检查
        self.assertEqual(sum(results), n_threads, "未复现：同一样本被多个线程同时接受")
        self.assertEqual(agg.total, n_threads, "未复现：同一样本被计入多次")

    def test_2_batch_retry_double_counted(self):
        """缺陷2：同一批次重试导致计数翻倍。"""
        agg = BuggyAggregator(yield_on_write=False)
        samples = [("s1", "dimA", 3), ("s2", "dimB", 4)]
        agg.apply_batch("batch-1", samples)
        agg.apply_batch("batch-1", samples)  # 网络重试，同一批次再次投递
        # 期望幂等（总数 7），实际翻倍为 14
        self.assertEqual(agg.total, 14, "未复现：批次重试未导致翻倍")

    def test_3_snapshot_reads_partial_state(self):
        """缺陷3：边写边读，统计输出读到中间态或抛 RuntimeError。"""
        agg = BuggyAggregator()
        done = threading.Event()
        inconsistencies = []
        errors = []

        def writer():
            for i in range(20000):
                agg.apply_batch(f"b{i}", [(f"s{i}", f"dim{i}", 1)])
                if i % 50 == 0:
                    time.sleep(0)  # 让出 GIL，制造读写交错窗口
            done.set()

        def reader():
            while not done.is_set():
                try:
                    snap = agg.snapshot()
                    if snap["total"] != sum(snap["dims"].values()):
                        inconsistencies.append(snap)
                except RuntimeError as e:
                    errors.append(e)

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join()
        rt.join()
        self.assertTrue(
            inconsistencies or errors,
            "未复现：并发读写未观察到中间态/迭代错误",
        )

    def test_4_failed_batch_partially_applied(self):
        """缺陷4：批次中途失败，前面的样本已计入但未上报。"""
        agg = BuggyAggregator(yield_on_write=False)
        samples = [("s1", "dimA", 5), ("s2", "dimB", -1)]  # 第二条非法
        with self.assertRaises(ValueError):
            agg.apply_batch("batch-x", samples)
        # 批次失败，但第一条样本的 5 已经计入
        self.assertEqual(agg.total, 5, "未复现：失败批次未留下部分计数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
