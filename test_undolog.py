"""undolog 自测：对拍、断点续滚、并发可见性、边界情形、性能。

运行：python3 -m unittest -v test_undolog.py
"""

import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from undolog import KeyQuarantined, TxError, TxStore

REPO = os.path.dirname(os.path.abspath(__file__))


class SnapshotRef:
    """参照实现：记录旧状态并整体恢复。"""

    def __init__(self):
        self.data = {}
        self._snap = None

    def begin(self):
        self._snap = dict(self.data)

    def set(self, k, v):
        self.data[k] = v

    def delete(self, k):
        self.data.pop(k, None)

    def commit(self):
        self._snap = None

    def rollback(self):
        self.data = self._snap
        self._snap = None


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "tx.log")

    def tearDown(self):
        self._tmp.cleanup()

    def open(self, **kw):
        return TxStore(self.path, **kw)


class TestBasic(Base):
    def test_empty_transaction_commit_and_rollback(self):
        s = self.open()
        tx = s.begin()
        tx.commit()
        tx2 = s.begin()
        tx2.rollback()  # 空事务回滚：no-op
        tx2.rollback()  # 幂等
        self.assertEqual(s.dump(), {})
        s.close()

    def test_single_operation(self):
        s = self.open()
        t0 = s.begin()
        t0.set("a", "old")
        t0.commit()
        tx = s.begin()
        tx.set("a", "new")   # 覆盖已有 key
        tx.set("b", 1)       # 新增 key
        tx.rollback()
        self.assertEqual(s.dump(), {"a": "old"})
        s.close()

    def test_reverse_order(self):
        s = self.open()
        tx = s.begin()
        for i in range(10):
            tx.set(f"k{i}", i)
        observed = []

        def hook(count):
            # 第 count 步撤销的是 seq = 9-count（逆序）
            done_seq = 9 - count
            observed.append(done_seq)
            # 已撤销的 key 应已不存在，未撤销的仍是新值
            for i in range(10):
                if i > done_seq:
                    self.assertNotIn(f"k{i}", s._data)
                else:
                    self.assertEqual(s._data.get(f"k{i}"), i)

        s._undo_hook = hook
        tx.rollback()
        self.assertEqual(observed, list(range(9, -1, -1)))  # 严格逆序
        self.assertEqual(s.dump(), {})
        s.close()

    def test_idempotent_rollback(self):
        s = self.open()
        tx = s.begin()
        tx.set("a", 1)
        tx.set("b", 2)
        tx.rollback()
        first = s.dump()
        tx.rollback()
        tx.rollback()
        self.assertEqual(s.dump(), first)
        with open(self.path, "rb") as f:
            log = f.read()
        self.assertEqual(log.count(b'"rollback_begin"'), 1)
        self.assertEqual(log.count(b'"rollback_done"'), 1)
        s.close()

    def test_rollback_committed_latest_tx(self):
        s = self.open()
        tx = s.begin()
        tx.set("a", 1)
        tx.commit()
        tx.rollback()  # 补偿：撤销最近一个已提交事务
        self.assertEqual(s.dump(), {})
        s.close()

    def test_state_errors(self):
        s = self.open()
        tx = s.begin()
        tx.commit()
        with self.assertRaises(TxError):
            tx.set("a", 1)
        with self.assertRaises(TxError):
            tx.commit()
        t2 = s.begin()
        with self.assertRaises(TxError):
            s.begin()  # 单写入者
        t2.rollback()
        s.close()


class TestDifferential(Base):
    def test_against_snapshot_reference(self):
        for trial in range(300):
            rng = random.Random(trial)
            # 存储文件在整个用例期间持续存在，且会被反复重启重放
            path = os.path.join(self._tmp.name, f"diff-{trial}.log")
            s = TxStore(path)
            ref = SnapshotRef()
            for _ in range(rng.randint(1, 6)):
                tx = s.begin()
                ref.begin()
                for _ in range(rng.choice([0, 1, rng.randint(2, 60)])):
                    k = f"k{rng.randint(0, 30)}"
                    if rng.random() < 0.25:
                        tx.delete(k)
                        ref.delete(k)
                    else:
                        v = rng.randint(-1000, 1000)
                        tx.set(k, v)
                        ref.set(k, v)
                if rng.random() < 0.5:
                    tx.commit()
                    ref.commit()
                else:
                    tx.rollback()
                    ref.rollback()
                self.assertEqual(s.dump(), ref.data, f"trial={trial}")
                if rng.random() < 0.25:
                    # 随机重启：从磁盘日志重放恢复，再与参照比对
                    s.close()
                    s = TxStore(path)
                    self.assertEqual(s.dump(), ref.data,
                                     f"trial={trial} reopen")
            s.close()
            # 磁盘日志必须真实落盘且非空
            self.assertGreater(os.path.getsize(path), 0)
            # 最终重启重放：recover 的结果必须与参照一致
            s = TxStore(path)
            self.assertEqual(s.dump(), ref.data, f"trial={trial} final reopen")
            s.close()


class TestResume(Base):
    def test_inprocess_failure_then_resume(self):
        s = self.open()
        t0 = s.begin()
        t0.set("base", 0)
        t0.commit()
        tx = s.begin()
        for i in range(1000):
            tx.set(f"k{i}", i)
        calls = [0]
        fail = [True]

        def hook(_):
            calls[0] += 1
            if fail[0] and calls[0] == 400:
                raise RuntimeError("injected rollback failure")

        s._undo_hook = hook
        with self.assertRaises(RuntimeError):
            tx.rollback()
        self.assertEqual(tx.state, "rolling")
        # 回滚未完成：新事务被拒绝，受影响 key 仍被隔离
        with self.assertRaises(TxError):
            s.begin()
        with self.assertRaises(KeyQuarantined):
            s.try_get("k0")
        # 断点续滚：从第 400 步继续，而不是从头再来
        fail[0] = False
        s.resume()
        self.assertEqual(tx.state, "rolled_back")
        # 每步恰好应用一次；失败的那一步重试，故 hook 调用 = 步数 + 失败次数
        self.assertEqual(calls[0], 1000 + 1)
        self.assertEqual(s.dump(), {"base": 0})
        s.close()

    def test_repeated_failures_during_rollback(self):
        s = self.open()
        tx = s.begin()
        for i in range(500):
            tx.set(f"k{i}", i)
        attempts = [0]
        fail = [True]

        def hook(_):
            attempts[0] += 1
            if fail[0] and attempts[0] in (100, 300):  # 回滚中再次失败
                raise RuntimeError("boom")

        s._undo_hook = hook
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                tx.rollback()
        fail[0] = False
        tx.rollback()  # 第三次完成
        self.assertEqual(attempts[0], 500 + 2)
        self.assertEqual(s.dump(), {})
        s.close()

    def test_crash_mid_rollback_then_recover(self):
        child = (
            "import sys,os;sys.path.insert(0,%r);import undolog;"
            "s=undolog.TxStore(sys.argv[1]);"
            "t=s.begin();"
            "t.set('pre','keep');t.commit();"
            "t2=s.begin();"
            "[t2.set('k%%d'%%i,i) for i in range(5000)];"
            "t2.commit();"
            "s._undo_hook=lambda c: os._exit(1) if c==1234 else None;"
            "t2.rollback()" % REPO
        )
        r = subprocess.run([sys.executable, "-c", child, self.path],
                           capture_output=True)
        self.assertEqual(r.returncode, 1)
        # 重启：recover 从断点续滚到完成
        s = self.open()
        self.assertEqual(s.dump(), {"pre": "keep"})
        with open(self.path, "rb") as f:
            log = f.read()
        self.assertEqual(log.count(b'"rollback_done"'), 1)
        # 日志中 undone 标记总数 == 操作数（续滚只补缺失部分）
        self.assertEqual(log.count(b'"undone"'), 5000)
        s.close()

    def test_crash_mid_transaction_aborts(self):
        child = (
            "import sys,os;sys.path.insert(0,%r);import undolog;"
            "s=undolog.TxStore(sys.argv[1]);"
            "t=s.begin();"
            "[t.set('k%%d'%%i,i) for i in range(200)];"
            "os._exit(1)" % REPO
        )
        r = subprocess.run([sys.executable, "-c", child, self.path],
                           capture_output=True)
        self.assertEqual(r.returncode, 1)
        s = self.open()
        self.assertEqual(s.dump(), {})  # 未提交事务被撤销
        s.close()

    def test_restart_continues_rollback(self):
        s = self.open()
        t0 = s.begin()
        t0.set("base", 1)
        t0.commit()
        tx = s.begin()
        for i in range(200):
            tx.set(f"k{i}", i)
        tx.delete("base")
        calls = [0]
        fail = [True]

        def hook(_):
            calls[0] += 1
            if fail[0] and calls[0] == 80:
                raise RuntimeError("injected rollback failure")

        s._undo_hook = hook
        with self.assertRaises(RuntimeError):
            tx.rollback()
        self.assertEqual(tx.state, "rolling")
        s.close()  # 回滚未完成即关闭，模拟重启
        # 重启：recover 从断点继续回滚到完成
        s2 = self.open()
        self.assertEqual(s2.dump(), {"base": 1})
        with open(self.path, "rb") as f:
            log = f.read()
        self.assertEqual(log.count(b'"rollback_begin"'), 1)
        self.assertEqual(log.count(b'"rollback_done"'), 1)
        # undone 标记总数 == 操作数（200 set + 1 delete），续滚只补缺失部分
        self.assertEqual(log.count(b'"undone"'), 201)
        s2.close()


class TestConcurrency(Base):
    def test_readers_never_see_intermediate_state(self):
        s = self.open()
        t0 = s.begin()
        t0.set("a", "OLD")
        t0.commit()
        tx = s.begin()
        tx.set("a", 1)
        tx.set("b", 2)
        started = threading.Event()
        release = threading.Event()

        def hook(count):
            if count == 0:
                started.set()
                self.assertTrue(release.wait(5))

        s._undo_hook = hook
        t = threading.Thread(target=tx.rollback)
        t.start()
        self.assertTrue(started.wait(5))
        # 回滚进行中：try_get 只能抛 KeyQuarantined 或读到旧值，绝不读中间值
        seen = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    seen.append(s.try_get("a"))
                except KeyQuarantined:
                    pass

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for r in readers:
            r.start()
        # 阻塞式 get 在回滚完成前不应返回
        blocked = []
        g = threading.Thread(target=lambda: blocked.append(s.get("a")))
        g.start()
        time.sleep(0.3)
        self.assertEqual(blocked, [])
        release.set()
        t.join(5)
        stop.set()
        for r in readers:
            r.join(5)
        g.join(5)
        self.assertEqual(blocked, ["OLD"])
        self.assertTrue(all(v == "OLD" for v in seen))
        self.assertEqual(s.dump(), {"a": "OLD"})
        s.close()


class TestScaleAndPerf(Base):
    def test_large_transaction_10k(self):
        s = self.open()
        tx = s.begin()
        for i in range(10_000):
            tx.set(f"k{i % 500}", i)
        tx.rollback()
        self.assertEqual(s.dump(), {})
        s.close()

    def test_perf_rollback_100k(self):
        s = self.open()
        tx = s.begin()
        t0 = time.perf_counter()
        for i in range(100_000):
            tx.set(f"k{i % 20_000}", i)
        t1 = time.perf_counter()
        tx.commit()
        t2 = time.perf_counter()
        tx.rollback()
        t3 = time.perf_counter()
        s.close()
        # 重启重放恢复也计时
        t4 = time.perf_counter()
        s2 = self.open()
        t5 = time.perf_counter()
        self.assertEqual(s2.dump(), {})
        s2.close()
        print(
            "\n[perf] 100k ops: log+apply %.3fs | commit %.3fs | "
            "rollback %.3fs (%.0f ops/s) | reopen+recover %.3fs"
            % (t1 - t0, t2 - t1, t3 - t2, 100_000 / (t3 - t2), t5 - t4)
        )


if __name__ == "__main__":
    unittest.main()
