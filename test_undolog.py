"""undolog 库的自测：对拍、断点续滚、并发可见性、边界情形、性能。

运行：python3 test_undolog.py -v
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from undolog import TransactionalKVStore


class RefModel:
    """参照实现：记录旧状态快照，回滚时整体恢复。"""

    def __init__(self):
        self.data = {}
        self._snap = None

    def begin(self):
        assert self._snap is None
        self._snap = dict(self.data)

    def set(self, key, value):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)

    def commit(self):
        self._snap = None

    def rollback(self):
        self.data = self._snap
        self._snap = None


# 子进程脚本：回滚到一半时 os._exit 模拟断电/进程被杀。
CRASH_CHILD = r"""
import os, sys
from undolog import TransactionalKVStore
store = TransactionalKVStore(sys.argv[1])
tx = store.begin()
for i in range(2000):
    tx.set("ck%05d" % i, i)
def hook(tx_id, seq):
    if seq == 1000:
        os._exit(1)  # 模拟崩溃：不做任何清理直接退出
tx.rollback(batch_size=1, after_apply=hook)
print("unreachable: rollback should have been interrupted")
"""


class UndoLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="undolog_test_")
        self.path = os.path.join(self.dir, "store.db")
        self.addCleanup(shutil.rmtree, self.dir, True)

    # ---------- 边界情形 ----------

    def test_empty_transaction(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        tx.rollback()
        tx.rollback()  # 重复回滚：幂等空操作
        self.assertEqual(store.dump(), {})
        self.assertEqual(store.tx_state(tx._id), "ROLLED_BACK")
        store.close()

    def test_single_operation(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        tx.set("a", 1)
        tx.rollback()
        self.assertEqual(store.dump(), {})
        tx2 = store.begin()
        tx2.set("a", 1)
        tx2.commit()
        tx3 = store.begin()
        tx3.delete("a")
        tx3.rollback()
        self.assertEqual(store.dump(), {"a": 1})
        store.close()

    def test_many_operations(self):
        store = TransactionalKVStore(self.path)
        ref = {}
        tx = store.begin()
        for i in range(10_000):
            tx.set("k%06d" % i, i)
            ref["k%06d" % i] = i
        tx.rollback()
        self.assertEqual(store.dump(), {})
        store.close()

    def test_overwrite_and_delete_restore_old_values(self):
        store = TransactionalKVStore(self.path)
        tx0 = store.begin()
        tx0.set("a", 1)
        tx0.set("b", 2)
        tx0.commit()
        tx = store.begin()
        tx.set("a", 100)   # 覆盖已有键
        tx.set("a", 200)   # 同一键多次写
        tx.delete("b")     # 删除已有键
        tx.set("c", 3)     # 新键
        tx.delete("c")     # 又删掉
        tx.rollback()
        self.assertEqual(store.dump(), {"a": 1, "b": 2})
        store.close()

    # ---------- 幂等 ----------

    def test_idempotent_double_rollback(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        for i in range(100):
            tx.set("k%d" % i, i)
        tx.rollback()
        first = store.dump()
        tx.rollback()
        tx.rollback()
        self.assertEqual(store.dump(), first)
        self.assertEqual(first, {})
        store.close()

    # ---------- 回滚中失败 / 再次失败 ----------

    def test_failure_during_rollback_then_resume(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        for i in range(100):
            tx.set("k%d" % i, i)
        calls = {"n": 0}

        def hook(tx_id, seq):
            calls["n"] += 1
            if calls["n"] == 50:
                raise RuntimeError("boom: rollback interrupted")

        with self.assertRaises(RuntimeError):
            tx.rollback(batch_size=1, after_apply=hook)
        self.assertEqual(store.tx_state(tx._id), "ROLLING_BACK")
        tx.rollback(batch_size=1)  # 断点续滚
        self.assertEqual(store.dump(), {})
        self.assertEqual(store.tx_state(tx._id), "ROLLED_BACK")
        store.close()

    def test_repeated_failures_during_rollback(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        for i in range(100):
            tx.set("k%d" % i, i)
        calls = {"n": 0}
        fail_at = {30, 60, 90}

        def hook(tx_id, seq):
            calls["n"] += 1
            if calls["n"] in fail_at:
                raise RuntimeError("boom %d" % calls["n"])

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                tx.rollback(batch_size=1, after_apply=hook)
        tx.rollback(batch_size=1)
        self.assertEqual(store.dump(), {})
        store.close()

    # ---------- 崩溃后断点续滚（真实子进程 os._exit） ----------

    def test_crash_recovery_subprocess(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.run(
            [sys.executable, "-c", CRASH_CHILD, self.path],
            env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(proc.returncode, 0, "child should crash mid-rollback")
        # 重新打开：recover 必须继续回滚到完成状态
        store = TransactionalKVStore(self.path)
        self.assertEqual(store.dump(), {})
        states = [r[0] for r in store._db.execute("SELECT DISTINCT state FROM tx")]
        self.assertEqual(states, ["ROLLED_BACK"])
        store.close()

    # ---------- 与参照实现对拍 ----------

    def test_differential_random(self):
        rng = random.Random(20260926)
        store = TransactionalKVStore(self.path)
        ref = RefModel()
        keys = ["k%d" % i for i in range(50)]
        for round_no in range(300):
            tx = store.begin()
            ref.begin()
            for _ in range(rng.randint(0, 40)):
                key = rng.choice(keys)
                if rng.random() < 0.7:
                    value = rng.randint(0, 10 ** 6)
                    tx.set(key, value)
                    ref.set(key, value)
                else:
                    tx.delete(key)
                    ref.delete(key)
            if rng.random() < 0.5:
                tx.commit()
                ref.commit()
            else:
                tx.rollback()
                ref.rollback()
            self.assertEqual(store.dump(), ref.data, "round %d mismatch" % round_no)
        store.close()

    # ---------- 并发可见性 ----------

    def test_reader_blocked_during_rollback(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        tx.set("a", 1)  # 未提交，"a" 被锁定
        started = threading.Event()
        allow = threading.Event()

        def hook(tx_id, seq):
            started.set()
            self.assertTrue(allow.wait(5))

        def do_rollback():
            tx.rollback(batch_size=1, after_apply=hook)

        rb = threading.Thread(target=do_rollback)
        rb.start()
        self.assertTrue(started.wait(5))

        result = {}

        def reader():
            result["v"] = store.get("a")  # 应阻塞，直到回滚完成

        rt = threading.Thread(target=reader)
        rt.start()
        time.sleep(0.3)
        self.assertTrue(rt.is_alive(), "reader must block, not see intermediate state")
        allow.set()
        rb.join(5)
        rt.join(5)
        self.assertIsNone(result["v"])  # 回滚后读到旧状态（键不存在）
        store.close()

    def test_reader_blocked_by_uncommitted_tx(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        tx.set("a", 42)
        entered = threading.Event()
        result = {}

        def reader():
            entered.set()
            result["v"] = store.get("a")

        rt = threading.Thread(target=reader)
        rt.start()
        self.assertTrue(entered.wait(5))
        time.sleep(0.2)
        self.assertTrue(rt.is_alive(), "reader must not see uncommitted write")
        tx.commit()
        rt.join(5)
        self.assertEqual(result["v"], 42)
        store.close()

    # ---------- 性能：十万次操作回滚 ----------

    def test_perf_rollback_100k(self):
        store = TransactionalKVStore(self.path)
        tx = store.begin()
        t0 = time.perf_counter()
        for i in range(100_000):
            tx.set("k%06d" % i, i)
        t1 = time.perf_counter()
        tx.rollback()
        t2 = time.perf_counter()
        rate = 100_000 / (t2 - t1)
        print("\n[perf] 记录 100k 操作(含撤销日志落盘): %.3fs" % (t1 - t0))
        print("[perf] 回滚 100k 操作: %.3fs (%.0f ops/s)" % (t2 - t1, rate))
        self.assertEqual(store.dump(), {})
        store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
