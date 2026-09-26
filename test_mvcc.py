"""MVCC 存储的自测：可见性断言、写写冲突、回收边界、交织场景。

运行：python3 -m unittest test_mvcc -v
"""

import unittest

from mvcc import (
    Store,
    Transaction,
    TransactionStateError,
    WriteConflictError,
)


def committed(store, key, value):
    """辅助：用一个独立事务写入并提交，返回提交时间戳。"""
    txn = store.begin()
    txn.put(key, value)
    return txn.commit()


class TestEmptyAndSingleTxn(unittest.TestCase):
    """空存储与单事务场景。"""

    def test_empty_store_read(self):
        store = Store()
        txn = store.begin()
        self.assertIsNone(txn.get("missing"))
        self.assertEqual(txn.get("missing", "dflt"), "dflt")
        txn.commit()

    def test_single_txn_read_own_writes_and_commit(self):
        store = Store()
        txn = store.begin()
        txn.put("a", 1)
        self.assertEqual(txn.get("a"), 1)  # read-your-own-writes
        txn.commit()
        # 提交后对新事务可见
        t2 = store.begin()
        self.assertEqual(t2.get("a"), 1)
        t2.commit()

    def test_delete(self):
        store = Store()
        committed(store, "a", 1)
        txn = store.begin()
        txn.delete("a")
        self.assertIsNone(txn.get("a"))
        txn.commit()
        t2 = store.begin()
        self.assertIsNone(t2.get("a"))
        t2.commit()

    def test_op_after_finish_raises(self):
        store = Store()
        txn = store.begin()
        txn.commit()
        with self.assertRaises(TransactionStateError):
            txn.get("a")
        with self.assertRaises(TransactionStateError):
            txn.put("a", 1)

    def test_read_only_txn_cannot_write(self):
        store = Store()
        txn = store.begin(read_only=True)
        with self.assertRaises(TransactionStateError):
            txn.put("a", 1)
        txn.commit()


class TestSnapshotVisibility(unittest.TestCase):
    """可见性规则：读事务看不到自己开始之后提交的版本。"""

    def test_reader_does_not_see_later_commits(self):
        store = Store()
        committed(store, "k", "v1")
        reader = store.begin()              # 快照 = 1
        committed(store, "k", "v2")         # 提交时间戳 = 2 > 1
        self.assertEqual(reader.get("k"), "v1")  # 仍看到旧版本
        reader.commit()
        t = store.begin()
        self.assertEqual(t.get("k"), "v2")  # 新事务看到新版本
        t.commit()

    def test_reader_does_not_see_uncommitted_writes(self):
        store = Store()
        committed(store, "k", "v1")
        writer = store.begin()
        writer.put("k", "dirty")
        reader = store.begin()
        self.assertEqual(reader.get("k"), "v1")
        reader.commit()
        writer.rollback()

    def test_snapshot_repeatable_read(self):
        store = Store()
        committed(store, "k", "v1")
        reader = store.begin()
        self.assertEqual(reader.get("k"), "v1")
        committed(store, "k", "v2")
        committed(store, "k", "v3")
        # 可重复读：同一事务内多次读结果一致
        self.assertEqual(reader.get("k"), "v1")
        self.assertEqual(reader.get("k"), "v1")
        reader.commit()

    def test_reader_does_not_see_later_delete(self):
        store = Store()
        committed(store, "k", "v1")
        reader = store.begin()
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        self.assertEqual(reader.get("k"), "v1")
        reader.commit()
        t = store.begin()
        self.assertIsNone(t.get("k"))
        t.commit()


class TestWriteWriteConflict(unittest.TestCase):
    """写-写冲突：first-committer-wins，后提交者被拒绝。"""

    def test_concurrent_writers_conflict(self):
        store = Store()
        committed(store, "k", "v0")
        t1 = store.begin()
        t2 = store.begin()
        t1.put("k", "from-t1")
        t2.put("k", "from-t2")
        t1.commit()  # 先提交者获胜
        with self.assertRaises(WriteConflictError):
            t2.commit()  # 后提交者被拒绝
        self.assertEqual(t2.state, Transaction.ABORTED)
        t = store.begin()
        self.assertEqual(t.get("k"), "from-t1")
        t.commit()

    def test_early_conflict_detection_on_put(self):
        store = Store()
        t1 = store.begin()
        t1.put("k", "x")
        t1.commit()
        t2 = store.begin()          # 快照在 t1 提交之后，无冲突
        t2.put("k", "y")
        t2.commit()
        t3 = store.begin()
        t4 = store.begin()
        t3.put("k", "z")
        t3.commit()
        with self.assertRaises(WriteConflictError):
            t4.put("k", "w")        # put 时即检测到冲突
        t4.rollback()

    def test_disjoint_keys_no_conflict(self):
        store = Store()
        t1 = store.begin()
        t2 = store.begin()
        t1.put("a", 1)
        t2.put("b", 2)
        t1.commit()
        t2.commit()  # 不同 key，不冲突
        t = store.begin()
        self.assertEqual((t.get("a"), t.get("b")), (1, 2))
        t.commit()


class TestRollback(unittest.TestCase):
    """回滚后重读：存储状态不变。"""

    def test_rollback_then_reread(self):
        store = Store()
        committed(store, "k", "v1")
        txn = store.begin()
        txn.put("k", "v2")
        txn.put("new", "x")
        txn.rollback()
        t = store.begin()
        self.assertEqual(t.get("k"), "v1")   # 旧值不变
        self.assertIsNone(t.get("new"))      # 未提交的插入不可见
        t.commit()
        # 回滚不产生新版本
        _, total, _, _ = store.version_stats()
        self.assertEqual(total, 1)

    def test_context_manager_rollback_on_exception(self):
        store = Store()
        committed(store, "k", "v1")
        with self.assertRaises(RuntimeError):
            with store.begin() as txn:
                txn.put("k", "v2")
                raise RuntimeError("boom")
        t = store.begin()
        self.assertEqual(t.get("k"), "v1")
        t.commit()


class TestLongReaderInterleavedWrites(unittest.TestCase):
    """只读长事务与高频写交织：长事务始终看到一致快照，且阻止回收。"""

    def test_long_reader_consistent_snapshot(self):
        store = Store()
        committed(store, "k", 0)
        reader = store.begin(read_only=True)
        for i in range(1, 200):
            committed(store, "k", i)
            if i % 10 == 0:
                store.collect_garbage()
            # 无论写多少次、回收多少次，长事务永远看到快照时刻的值
            self.assertEqual(reader.get("k"), 0)
        reader.commit()
        t = store.begin()
        self.assertEqual(t.get("k"), 199)
        t.commit()

    def test_gc_blocked_by_long_transaction(self):
        store = Store()
        committed(store, "k", 0)             # ts=1
        reader = store.begin(read_only=True)  # 快照 = 1，watermark 被钉在 1
        for i in range(1, 50):
            committed(store, "k", i)
        reclaimed = store.collect_garbage()
        self.assertEqual(reclaimed, 0)       # 长事务活着，一个版本都不能回收
        _, total, max_chain, _ = store.version_stats()
        self.assertEqual(total, 50)          # 50 个版本全部保留
        self.assertEqual(max_chain, 50)
        self.assertEqual(reader.get("k"), 0)
        reader.commit()
        # 长事务结束后，回收立刻生效
        reclaimed = store.collect_garbage()
        self.assertEqual(reclaimed, 49)
        _, total, _, _ = store.version_stats()
        self.assertEqual(total, 1)
        t = store.begin()
        self.assertEqual(t.get("k"), 49)
        t.commit()


class TestGarbageCollectionBoundary(unittest.TestCase):
    """回收边界：end_ts <= watermark 回收，end_ts > watermark 保留。"""

    def test_boundary_exact(self):
        store = Store()
        committed(store, "k", "v1")          # ts=1
        committed(store, "k", "v2")          # ts=2，v1.end_ts=2
        reader = store.begin()               # 快照 = 2 => watermark = 2
        committed(store, "k", "v3")          # ts=3，v2.end_ts=3
        reclaimed = store.collect_garbage()
        # v1.end_ts=2 <= watermark=2 → 回收；v2.end_ts=3 > 2 → 保留
        self.assertEqual(reclaimed, 1)
        chain = store._data["k"]
        self.assertEqual([v.value for v in chain], ["v3", "v2"])
        self.assertEqual(reader.get("k"), "v2")  # 边界上的版本仍可读
        reader.commit()

    def test_no_active_txn_reclaims_all_superseded(self):
        store = Store()
        for i in range(10):
            committed(store, "k", i)
        reclaimed = store.collect_garbage()
        self.assertEqual(reclaimed, 9)
        _, total, _, _ = store.version_stats()
        self.assertEqual(total, 1)

    def test_gc_reclaims_tombstoned_key(self):
        store = Store()
        committed(store, "k", "v1")
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        reclaimed = store.collect_garbage()
        self.assertEqual(reclaimed, 2)       # 旧版本 + 墓碑整体移除
        self.assertEqual(store.version_stats(), (0, 0, 0, 0.0))
        t = store.begin()
        self.assertIsNone(t.get("k"))
        t.commit()

    def test_gc_preserves_keys_still_visible(self):
        store = Store()
        committed(store, "a", 1)
        committed(store, "b", 2)
        reader = store.begin()               # 快照 = 2
        committed(store, "a", 10)
        store.collect_garbage()
        self.assertEqual(reader.get("a"), 1)
        self.assertEqual(reader.get("b"), 2)
        reader.commit()

    def test_memory_after_gc(self):
        """回收后的内存数据：只剩每个 key 的最新版本。"""
        store = Store()
        for i in range(1000):
            committed(store, f"key-{i % 10}", i)
        store.collect_garbage()
        keys, total, max_chain, avg = store.version_stats()
        self.assertEqual((keys, total, max_chain), (10, 10, 1))
        self.assertEqual(avg, 1.0)


if __name__ == "__main__":
    unittest.main()
