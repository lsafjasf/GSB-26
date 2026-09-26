"""filelease 自测：代际安全 / 强杀恢复 / 并发争抢 / 续期 / 时钟回拨 / 文件损坏。

运行: python3 -m unittest test_filelease -v
"""

import json
import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filelease import (AcquireTimeout, ClockDriftDetected, FileLeaseLock,
                       LeaseExpired, LeaseLost, LeaseStolen)


class FakeClock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FencedStore:
    """模拟下游资源：只接受严格递增的 fencing token。"""

    def __init__(self):
        self.last_token = 0
        self.data = None
        self.rejected = []

    def write(self, token, value):
        if token <= self.last_token:
            self.rejected.append((token, value))
            raise LeaseStolen("stale fencing token %d (last %d)"
                              % (token, self.last_token))
        self.last_token = token
        self.data = value


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class TestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="filelease-test-")
        self.lock_path = os.path.join(self.dir, "res.lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_lock(self, holder="holder", ttl=5.0, **kw):
        return FileLeaseLock(self.lock_path, holder_id=holder, ttl=ttl, **kw)


class TestBasic(TestBase):
    def test_acquire_renew_release(self):
        lock = self.make_lock("A", ttl=0.4)
        lease = lock.acquire(timeout=1.0)
        self.assertEqual(lease.generation, 1)
        for _ in range(5):                      # 反复续期
            time.sleep(0.1)
            new_exp = lease.renew()
            self.assertGreater(new_exp, time.time())
        lease.release()
        # 释放后他人立即可获取，且代际递增
        lock2 = self.make_lock("B", ttl=0.4)
        lease2 = lock2.acquire(timeout=1.0)
        self.assertEqual(lease2.generation, 2)
        lease2.release()

    def test_mutual_exclusion_while_held(self):
        lock_a = self.make_lock("A", ttl=5.0)
        lease = lock_a.acquire(timeout=1.0)
        lock_b = self.make_lock("B", ttl=5.0)
        self.assertIsNone(lock_b.try_acquire())
        with self.assertRaises(AcquireTimeout):
            lock_b.acquire(timeout=0.3)
        lease.release()
        self.assertIsNotNone(lock_b.try_acquire())


class TestGenerationSafety(TestBase):
    """核心安全测试：旧持有者被抢占后，续期与受保护写入都必须被拒绝。"""

    def test_stale_holder_detected_after_preemption(self):
        store = FencedStore()
        lock_old = self.make_lock("OLD", ttl=0.3)
        lease_old = lock_old.acquire(timeout=1.0)
        tok_old = lease_old.fencing_token
        store.write(tok_old, "old-write-1")          # 持锁期间写入成功
        self.assertEqual(store.data, "old-write-1")

        time.sleep(0.5)                               # 旧持有者“卡住”，租约过期
        lock_new = self.make_lock("NEW", ttl=5.0)
        lease_new = lock_new.acquire(timeout=2.0)     # 抢占成功
        tok_new = lease_new.fencing_token
        self.assertGreater(tok_new, tok_old)          # 代际单调递增

        # 1) 旧持有者尝试续期 -> 必须被识别为被抢占
        with self.assertRaises(LeaseStolen):
            lease_old.renew()
        # 2) 旧持有者尝试释放别人的锁 -> 拒绝
        with self.assertRaises(LeaseStolen):
            lease_old.release()
        # 3) 旧持有者仍尝试写入下游资源 -> fencing token 拒绝
        with self.assertRaises(LeaseStolen):
            store.write(tok_old, "stale-write")
        # 4) 新持有者写入成功
        store.write(tok_new, "new-write")
        self.assertEqual(store.data, "new-write")
        self.assertEqual(store.rejected, [(tok_old, "stale-write")])
        lease_new.release()

    def test_tampered_content_detected(self):
        lock = self.make_lock("A", ttl=5.0)
        lease = lock.acquire(timeout=1.0)
        # 外部篡改锁文件内容（如另一持有者覆写）
        with open(self.lock_path) as fh:
            rec = json.load(fh)
        rec["holder"] = "ATTACKER"
        rec["generation"] += 1
        with open(self.lock_path, "w") as fh:
            json.dump(rec, fh)
        with self.assertRaises(LeaseStolen):
            lease.renew()

    def test_deleted_file_detected(self):
        lock = self.make_lock("A", ttl=5.0)
        lease = lock.acquire(timeout=1.0)
        os.unlink(self.lock_path)
        with self.assertRaises(LeaseLost):
            lease.renew()


class TestClockRollback(TestBase):
    def test_clock_rollback_detected_on_renew(self):
        wall, mono = FakeClock(), FakeClock()
        lock = self.make_lock("A", ttl=10.0, drift_tolerance=0.5,
                              time_fn=wall, mono_fn=mono)
        lease = lock.acquire(timeout=1.0)
        wall.advance(3.0); mono.advance(3.0)
        lease.renew()                                 # 正常续期
        wall.advance(-8.0)                            # 墙钟回拨 8 秒
        mono.advance(1.0)
        with self.assertRaises(ClockDriftDetected):
            lease.renew()
        # 漂移后租约句柄必须作废，不允许静默继续
        with self.assertRaises(LeaseLost):
            lease.ensure_valid()

    def test_rollback_does_not_extend_lease_for_others(self):
        """回拨时钟下，抢占方也不应提前抢到未过期的锁（失败方向是安全的）。"""
        wall, mono = FakeClock(), FakeClock()
        lock_a = self.make_lock("A", ttl=10.0, time_fn=wall, mono_fn=mono)
        lock_a.acquire(timeout=1.0)
        wall.advance(-100.0)                          # 时钟大幅回拨
        lock_b = self.make_lock("B", ttl=10.0, time_fn=wall, mono_fn=mono)
        self.assertIsNone(lock_b.try_acquire())       # 锁看起来仍未过期 -> 不抢


class TestCorruption(TestBase):
    def test_corrupted_lock_file_recovered(self):
        lock_a = self.make_lock("A", ttl=5.0)
        lease = lock_a.acquire(timeout=1.0)
        gen_before = lease.generation
        lease.release()
        with open(self.lock_path, "wb") as fh:        # 写坏锁文件
            fh.write(b"\x00\xff not json {{{")
        lock_b = self.make_lock("B", ttl=5.0)
        lease_b = lock_b.acquire(timeout=2.0)         # 损坏后仍可获取
        self.assertGreaterEqual(lease_b.generation, gen_before + 1)
        lease_b.release()
        tombs = [f for f in os.listdir(self.dir) if ".corrupt-" in f]
        self.assertTrue(tombs, "损坏文件应被隔离保留")


# ---------------- 多进程辅助 ----------------

def _contend_worker(lock_path, holder, ttl, rounds, result_dir):
    lock = FileLeaseLock(lock_path, holder_id=holder, ttl=ttl)
    lines = []
    for _ in range(rounds):
        t0 = time.monotonic()
        lease = lock.acquire(timeout=60.0)
        t1 = time.monotonic()
        time.sleep(0.01)                              # 模拟临界区工作
        lease.release()
        t2 = time.monotonic()
        lines.append("%.6f %.6f %.6f %s\n" % (t1, t2, t1 - t0, holder))
    with open(os.path.join(result_dir, holder + ".log"), "w") as fh:
        fh.writelines(lines)


class TestContention(TestBase):
    PROCS = 6
    ROUNDS = 4

    def test_contention_no_overlap_and_latency(self):
        result_dir = os.path.join(self.dir, "results")
        os.mkdir(result_dir)
        procs = [mp.Process(target=_contend_worker,
                            args=(self.lock_path, "w%d" % i, 0.5, self.ROUNDS, result_dir))
                 for i in range(self.PROCS)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
            self.assertEqual(p.exitcode, 0)

        intervals, latencies = [], []
        for name in os.listdir(result_dir):
            with open(os.path.join(result_dir, name)) as fh:
                for line in fh:
                    start, end, lat, holder = line.split()
                    intervals.append((float(start), float(end), holder))
                    latencies.append(float(lat))
        self.assertEqual(len(intervals), self.PROCS * self.ROUNDS)

        # 互斥性：按开始时间排序后，任意相邻临界区不得重叠
        intervals.sort()
        for (s1, e1, h1), (s2, e2, h2) in zip(intervals, intervals[1:]):
            self.assertLessEqual(e1, s2 + 1e-9,
                                 "临界区重叠: %s[%f,%f] vs %s[%f,%f]"
                                 % (h1, s1, e1, h2, s2, e2))

        latencies.sort()
        print("\n[争抢耗时分布] %d 进程 x %d 轮, 共 %d 次获取 (秒):"
              % (self.PROCS, self.ROUNDS, len(latencies)))
        print("  min=%.4f p50=%.4f p90=%.4f p99=%.4f max=%.4f mean=%.4f"
              % (latencies[0], percentile(latencies, 0.50),
                 percentile(latencies, 0.90), percentile(latencies, 0.99),
                 latencies[-1], sum(latencies) / len(latencies)))


class TestKillRecovery(TestBase):
    """强杀恢复：子进程持锁后被 kill -9，父进程在租约到期后抢锁成功，输出时间线。"""

    def test_sigkill_recovery_timeline(self):
        ttl = 1.5
        marker = os.path.join(self.dir, "marker")
        child_code = (
            "import sys, time, os\n"
            "sys.path.insert(0, %r)\n"
            "from filelease import FileLeaseLock\n"
            "lock = FileLeaseLock(%r, holder_id='victim', ttl=%r)\n"
            "lease = lock.acquire(timeout=5)\n"
            "open(%r, 'w').write('%%.6f' %% time.time())\n"
            "time.sleep(60)\n"
            % (os.path.dirname(os.path.abspath(__file__)), self.lock_path, ttl, marker))

        t_parent0 = time.time()
        child = subprocess.Popen([sys.executable, "-c", child_code])
        try:
            deadline = time.time() + 10
            while not os.path.exists(marker):
                self.assertLess(time.time(), deadline, "子进程未能获取锁")
                time.sleep(0.02)
            with open(marker) as fh:
                t_child_acquired = float(fh.read())

            time.sleep(0.2)
            t_kill = time.time()
            child.send_signal(signal.SIGKILL)           # 强杀
            child.wait()
            self.assertEqual(child.returncode, -signal.SIGKILL)

            # 到期前抢锁必须失败
            lock_parent = self.make_lock("parent", ttl=5.0)
            self.assertIsNone(lock_parent.try_acquire())

            lease = lock_parent.acquire(timeout=ttl + 5.0)
            t_acquired = time.time()
            lease.release()
        finally:
            if child.poll() is None:
                child.kill()

        expected_expiry = t_child_acquired + ttl
        timeline = [
            ("父进程启动子进程", t_parent0),
            ("子进程获取锁 (gen=1)", t_child_acquired),
            ("子进程被 kill -9", t_kill),
            ("租约到期(预期)", expected_expiry),
            ("父进程抢占成功 (gen=2)", t_acquired),
        ]
        print("\n[强杀恢复时间线] ttl=%.1fs" % ttl)
        for label, t in timeline:
            print("  %s  t=%.3fs (相对启动)" % (label, t - t_parent0))
        print("  kill->恢复 耗时: %.3fs (理论下限 %.3fs)"
              % (t_acquired - t_kill, expected_expiry - t_kill))

        # 恢复时刻不得早于租约到期（允许 50ms 时钟/调度误差）
        self.assertGreaterEqual(t_acquired, expected_expiry - 0.05)
        self.assertLess(t_acquired, expected_expiry + 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
