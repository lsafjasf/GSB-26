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
        # 300 组随机序列全部打在同一份持续存在的磁盘日志上，
        # 覆盖 set/delete/commit/rollback，并周期性重启走 recover 重放。
        s = self.open()
        ref = SnapshotRef()
        crash_child = (
            "import sys;sys.path.insert(0,%r);import undolog;"
            "s=undolog.TxStore(sys.argv[1]);"
            "t=s.begin();"
            "t.set('k0','crash');t.set('junk','x');"
            "os._exit(1)" % REPO
        )
        reopen_count = 0
        for trial in range(300):
            rng = random.Random(trial)
            if trial in (77, 222):
                # 真杀进程：事务未提交即崩溃，重开后该事务应被整体撤销
                s.close()
                r = subprocess.run(
                    [sys.executable, "-c", "import os;" + crash_child, self.path],
                    capture_output=True)
                self.assertEqual(r.returncode, 1)
                s = self.open()
                reopen_count += 1
                self.assertEqual(s.dump(), ref.data, f"trial={trial} crash-abort")
                continue
            if trial % 50 == 49:
                # 干净重启：重放磁盘日志重建状态
                s.close()
                s = self.open()
                reopen_count += 1
                self.assertEqual(s.dump(), ref.data, f"trial={trial} reopen")
            for _ in range(rng.randint(1, 6)):
                tx = s.begin()
                ref.begin()
                for _ in range(rng.choice([0, 1, rng.randint(2, 60)])):
                    k = f"k{rng.randint(0, 30)}"
                    if rng.random() < 0.3:
                        tx.delete(k)
                        ref.delete(k)
                    else:
                        v = rng.choice([
                            rng.randint(-1000, 1000),
                            f"v{rng.randint(0, 999)}",
                            [rng.randint(0, 9), "x"],
                            {"n": rng.randint(0, 9)},
                            None, True,
                        ])
                        tx.set(k, v)
                        ref.set(k, v)
                    # 逐条比对：每个操作后全量状态与参照一致
                    self.assertEqual(s.dump(), ref.data, f"trial={trial} mid-tx")
                if rng.random() < 0.5:
                    tx.commit()
                    ref.commit()
                else:
                    tx.rollback()
                    ref.rollback()
                self.assertEqual(s.dump(), ref.data, f"trial={trial}")
        s.close()
        # 存储文件确实持续存在且承载了全部随机序列的日志
        self.assertTrue(os.path.exists(self.path))
        self.assertGreater(os.path.getsize(self.path), 0)
        self.assertGreaterEqual(reopen_count, 7)


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

    def test_restart_then_continue_rollback(self):
        # 回滚被真杀进程打断 -> 重启后续滚到完成 -> 新事务提交 -> 再次
        # 回滚中崩溃 -> 再重启动续滚。两轮跨重启断点续滚打在同一份日志上。
        crash_child = (
            "import sys,os;sys.path.insert(0,%r);import undolog;"
            "s=undolog.TxStore(sys.argv[1]);"
            "t=s.begin();"
            "[t.set('k%%d'%%i,i) for i in range(300)];"
            "t.commit();"
            "s._undo_hook=lambda c: os._exit(1) if c==int(sys.argv[2]) else None;"
            "t.rollback()" % REPO
        )

        def crash_rollback(stop_at):
            return subprocess.run(
                [sys.executable, "-c", crash_child, self.path, str(stop_at)],
                capture_output=True)

        # 基线数据（已提交，任何回滚都不应影响它）
        s = self.open()
        t0 = s.begin()
        t0.set("base", "keep")
        t0.commit()
        s.close()

        # 第一轮：回滚到第 100 步时硬崩溃，重启后从断点续滚
        r = crash_rollback(100)
        self.assertEqual(r.returncode, 1)
        s = self.open()
        self.assertEqual(s.dump(), {"base": "keep"})
        # 续滚完成后可以开新事务并提交
        t1 = s.begin()
        t1.set("after", 1)
        t1.commit()
        s.close()

        # 第二轮：再次回滚中崩溃（第 250 步），重启后续滚
        r = crash_rollback(250)
        self.assertEqual(r.returncode, 1)
        s = self.open()
        self.assertEqual(s.dump(), {"base": "keep", "after": 1})
        s.close()

        # 两轮各 300 个操作，undone 标记恰好各 300 个（续滚只补缺失部分），
        # rollback_done 各出现一次
        with open(self.path, "rb") as f:
            log = f.read()
        self.assertEqual(log.count(b'"undone"'), 600)
        self.assertEqual(log.count(b'"rollback_done"'), 2)


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
