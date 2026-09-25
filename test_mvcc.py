"""MVCC 可见性断言测试 + 版本回收边界测试。

运行：python3 -m unittest test_mvcc -v
"""

import unittest

from mvcc import (
    MVCCStore,
    TransactionStateError,
    WriteConflictError,
    is_visible,
)


class TestVisibilityRule(unittest.TestCase):
    """可见性规则本身的断言。"""

    def test_rule_function(self):
        # 版本对快照可见 <=> commit_version <= snapshot
        self.assertTrue(is_visible(1, 1))
        self.assertTrue(is_visible(3, 5))
        self.assertFalse(is_visible(5, 3))
        self.assertFalse(is_visible(1, 0))
        with self.assertRaises(AssertionError):
            is_visible(0, 0)  # 版本号从 1 开始

    def test_reader_cannot_see_versions_committed_after_begin(self):
        """核心断言：读事务看不到自己 begin 之后提交的版本。"""
        store = MVCCStore()
        t0 = store.begin()
        t0.put("k", "v1")
        t0.commit()

        reader = store.begin()          # 快照 = v1 提交点
        t2 = store.begin()              # 在 reader 之后开始
        t2.put("k", "v2")
        t2.commit()                     # reader begin 之后提交

        self.assertEqual(reader.get("k"), "v1")   # 仍是旧快照
        t3 = store.begin()
        self.assertEqual(t3.get("k"), "v2")       # 新事务可见
        reader.rollback()
        t3.rollback()

    def test_snapshot_isolation_repeatable_read(self):
        """同一事务内重复读结果一致（可重复读）。"""
        store = MVCCStore()
        with store.begin() as t:
            t.put("a", 1)
        r = store.begin()
        first = r.get("a")
        for i in range(2, 50):          # 高频并发写
            with store.begin() as w:
                w.put("a", i)
            self.assertEqual(r.get("a"), first)
        r.rollback()

    def test_read_your_own_writes(self):
        store = MVCCStore()
        tx = store.begin()
        tx.put("k", "mine")
        self.assertEqual(tx.get("k"), "mine")
        tx.delete("k")
        self.assertIsNone(tx.get("k"))
        tx.rollback()


class TestBasicOperations(unittest.TestCase):
    """空存储与单事务。"""

    def test_empty_store(self):
        store = MVCCStore()
        tx = store.begin()
        self.assertIsNone(tx.get("missing"))
        self.assertEqual(tx.get("missing", "dflt"), "dflt")
        tx.commit()                     # 空写集提交合法
        self.assertEqual(store.total_versions(), 0)
        self.assertEqual(store.gc(), 0)  # 空存储 GC 无操作

    def test_single_transaction_commit(self):
        store = MVCCStore()
        tx = store.begin()
        tx.put("x", 10)
        tx.put("y", 20)
        tx.commit()
        self.assertEqual(tx.state, "committed")
        r = store.begin()
        self.assertEqual(r.get("x"), 10)
        self.assertEqual(r.get("y"), 20)
        r.rollback()

    def test_single_transaction_delete(self):
        store = MVCCStore()
        with store.begin() as t:
            t.put("x", 1)
        with store.begin() as t:
            t.delete("x")
        r = store.begin()
        self.assertIsNone(r.get("x"))
        r.rollback()
        # 墓碑也是版本，GC 前链上有 2 个版本
        self.assertEqual(store.chain_length("x"), 2)

    def test_closed_transaction_rejects_ops(self):
        store = MVCCStore()
        tx = store.begin()
        tx.rollback()
        for op in (lambda: tx.get("k"), lambda: tx.put("k", 1),
                   lambda: tx.commit(), lambda: tx.rollback()):
            with self.assertRaises(TransactionStateError):
                op()


class TestRollback(unittest.TestCase):
    """回滚后重读：数据必须回到回滚前的状态。"""

    def test_rollback_then_reread(self):
        store = MVCCStore()
        with store.begin() as t:
            t.put("k", "stable")

        tx = store.begin()
        tx.put("k", "dirty")
        tx.put("new", "dirty")
        self.assertEqual(tx.get("k"), "dirty")   # 自己可见
        tx.rollback()
        self.assertEqual(tx.state, "aborted")

        r = store.begin()
        self.assertEqual(r.get("k"), "stable")   # 回滚后重读：旧值
        self.assertIsNone(r.get("new"))          # 未提交的 key 不存在
        r.rollback()
        self.assertEqual(store.chain_length("k"), 1)  # 未产生新版本

    def test_rollback_empty_store(self):
        store = MVCCStore()
        tx = store.begin()
        tx.put("k", 1)
        tx.rollback()
        r = store.begin()
        self.assertIsNone(r.get("k"))
        r.rollback()
        self.assertEqual(store.total_versions(), 0)


class TestWriteWriteConflict(unittest.TestCase):
    """写写冲突策略：first-committer-wins，后提交者被拒绝并回滚。"""

    def test_concurrent_writers_conflict(self):
        store = MVCCStore()
        with store.begin() as t:
            t.put("k", "init")

        a = store.begin()   # 同一快照出发
        b = store.begin()
        a.put("k", "A")
        b.put("k", "B")
        a.commit()          # 先提交者获胜
        with self.assertRaises(WriteConflictError):
            b.commit()      # 后提交者被拒绝
        self.assertEqual(b.state, "aborted")  # 冲突后事务已回滚

        r = store.begin()
        self.assertEqual(r.get("k"), "A")
        r.rollback()

    def test_disjoint_keys_no_conflict(self):
        store = MVCCStore()
        a = store.begin()
        b = store.begin()
        a.put("k1", 1)
        b.put("k2", 2)
        a.commit()
        b.commit()          # 不同 key 不冲突
        self.assertEqual(a.state, "committed")
        self.assertEqual(b.state, "committed")

    def test_sequential_writers_no_conflict(self):
        """后开始的事务快照包含先提交版本，不算冲突。"""
        store = MVCCStore()
        with store.begin() as t:
            t.put("k", 1)
        with store.begin() as t:
            t.put("k", 2)
        r = store.begin()
        self.assertEqual(r.get("k"), 2)
        r.rollback()

    def test_read_write_no_conflict(self):
        """读写不互斥：纯读事务永不阻塞写。"""
        store = MVCCStore()
        r = store.begin()
        r.get("k")
        with store.begin() as w:
            w.put("k", 1)
        r.rollback()


class TestLongReaderVsWriters(unittest.TestCase):
    """只读长事务与高频写交织：长事务期间快照稳定，写不被阻塞。"""

    def test_long_reader_interleaved_with_writes(self):
        store = MVCCStore()
        with store.begin() as t:
            t.put("hot", 0)

        reader = store.begin()          # 长事务，快照固定在 v1
        for i in range(1, 2000):        # 高频写
            with store.begin() as w:
                w.put("hot", i)
            if i % 100 == 0:
                self.assertEqual(reader.get("hot"), 0)  # 快照始终不变
        self.assertEqual(reader.get("hot"), 0)
        self.assertGreater(store.chain_length("hot"), 1000)  # 版本在堆积
        reader.rollback()

        r2 = store.begin()
        self.assertEqual(r2.get("hot"), 1999)
        r2.rollback()


class TestGarbageCollection(unittest.TestCase):
    """版本回收：基于活跃事务的最小快照。"""

    def _fill(self, store, key, n):
        for i in range(n):
            with store.begin() as t:
                t.put(key, i)

    def test_gc_condition_and_result(self):
        """回收条件：无活跃事务时 min_snap = 当前 clock，
        每个 key 只保留最新版本。"""
        store = MVCCStore()
        self._fill(store, "k", 10)
        self.assertEqual(store.chain_length("k"), 10)

        removed = store.gc()
        self.assertEqual(removed, 9)                 # 回收 9 个旧版本
        self.assertEqual(store.chain_length("k"), 1)  # 回收后内存数据
        r = store.begin()
        self.assertEqual(r.get("k"), 9)              # 最新值仍可读
        r.rollback()

    def test_gc_keeps_newest_visible_to_min_snapshot(self):
        """min_snap 落在链中间时：保留 <= min_snap 的最新版本及其后的。"""
        store = MVCCStore()
        self._fill(store, "k", 5)        # v1..v5
        reader = store.begin()           # 快照 = 5
        self._fill(store, "k", 5)        # v6..v10
        self.assertEqual(store.chain_length("k"), 10)

        removed = store.gc()             # min_snap = 5
        self.assertEqual(removed, 4)     # 删 v1..v4，保留 v5..v10
        self.assertEqual(store.chain_length("k"), 6)
        self.assertEqual(reader.get("k"), 4)  # 长事务快照数据完好
        reader.rollback()

    def test_long_transaction_blocks_gc(self):
        """长事务存在时，其可见的旧版本不得被回收。"""
        store = MVCCStore()
        with store.begin() as t:
            t.put("k", "genesis")        # v1

        long_reader = store.begin()      # 快照 = 1，钉住 v1
        self._fill(store, "k", 100)      # v2..v101

        removed = store.gc()
        self.assertEqual(removed, 0)     # min_snap = 1，一个都不能删
        self.assertEqual(store.chain_length("k"), 101)
        self.assertEqual(long_reader.get("k"), "genesis")  # 旧版本还在

        long_reader.rollback()           # 长事务结束，解除阻塞
        removed = store.gc()
        self.assertEqual(removed, 100)
        self.assertEqual(store.chain_length("k"), 1)
        r = store.begin()
        self.assertEqual(r.get("k"), 99)
        r.rollback()

    def test_gc_never_breaks_active_readers(self):
        """GC 前后所有活跃事务的读取结果不变（回归断言）。"""
        store = MVCCStore()
        self._fill(store, "k", 3)                    # v1..v3
        readers = [store.begin() for _ in range(3)]  # 快照 1,2,3
        expected = [r.get("k") for r in readers]
        self._fill(store, "k", 3)                    # v4..v6
        store.gc()
        for r, want in zip(readers, expected):
            self.assertEqual(r.get("k"), want)       # GC 后读结果一致
        for r in readers:
            r.rollback()

    def test_gc_removes_tombstone_when_safe(self):
        store = MVCCStore()
        with store.begin() as t:
            t.put("k", 1)
        with store.begin() as t:
            t.delete("k")
        self.assertEqual(store.chain_length("k"), 2)
        store.gc()
        # 无活跃事务：墓碑之前的版本可回收，但墓碑本身需保留
        # （否则未来的读会把不存在的 key 与"曾存在"混淆——此处链保留墓碑）
        self.assertEqual(store.chain_length("k"), 1)
        r = store.begin()
        self.assertIsNone(r.get("k"))
        r.rollback()


if __name__ == "__main__":
    unittest.main()
