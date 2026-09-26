"""
lease_lock 自测套件。

运行：
    python3 -m unittest -v test_lease_lock
    python3 test_lease_lock.py            # 同上，-v 输出
"""

from __future__ import annotations

import json
import math
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lease_lock import (
    AcquireTimeoutError,
    ClockDriftError,
    FencedResource,
    LeaseLock,
    LockLostError,
    PreemptedError,
    StaleGenerationError,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def pct(sorted_data, p):
    idx = min(len(sorted_data) - 1, max(0, math.ceil(p * len(sorted_data)) - 1))
    return sorted_data[idx]


def describe_dist(data, unit_ms=True):
    s = sorted(data)
    scale = 1000.0 if unit_ms else 1.0
    suffix = "ms" if unit_ms else "s"
    return (
        f"n={len(s)} min={s[0]*scale:.1f}{suffix} p50={pct(s,0.50)*scale:.1f}{suffix} "
        f"p90={pct(s,0.90)*scale:.1f}{suffix} p99={pct(s,0.99)*scale:.1f}{suffix} "
        f"max={s[-1]*scale:.1f}{suffix} mean={(sum(s)/len(s))*scale:.1f}{suffix}"
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lease_lock_test_")
        self.lock_path = os.path.join(self.dir, "res.lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- 基础语义

class TestBasic(Base):
    def test_acquire_renew_release(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=1.0)
        lock.acquire(timeout=1.0)
        self.assertTrue(lock.is_held)
        self.assertEqual(lock.generation, 1)
        new_expiry = lock.renew()
        self.assertGreater(new_expiry, time.time())
        lock.release()
        self.assertFalse(lock.is_held)
        self.assertFalse(os.path.exists(self.lock_path))

    def test_mutual_exclusion_and_generation_increments(self):
        a = LeaseLock(self.lock_path, holder_id="A", lease_duration=5.0)
        b = LeaseLock(self.lock_path, holder_id="B", lease_duration=5.0)
        a.acquire(timeout=1.0)
        self.assertFalse(b.try_acquire())
        with self.assertRaises(AcquireTimeoutError):
            b.acquire(timeout=0.2, retry_interval=0.05)
        a.release()
        self.assertTrue(b.try_acquire())
        self.assertEqual(b.generation, 2)  # 代际单调递增
        b.release()

    def test_repeated_renew_keeps_generation_and_extends_expiry(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=0.4)
        lock.acquire(timeout=1.0)
        gen = lock.generation
        last_expiry = 0.0
        for _ in range(20):  # 反复续期
            time.sleep(0.05)
            exp = lock.renew()
            self.assertGreaterEqual(exp, last_expiry)
            last_expiry = exp
            self.assertEqual(lock.generation, gen)
            self.assertTrue(lock.validate())
        lock.release()

    def test_auto_renew_thread(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=0.3)
        lock.acquire(timeout=1.0)
        lost = []
        lock.start_auto_renew(interval=0.1, on_lost=lost.append)
        time.sleep(0.8)  # 远超一个租约周期，靠自动续期保持
        self.assertTrue(lock.validate())
        self.assertEqual(lost, [])
        lock.stop_auto_renew()
        lock.release()


# ---------------------------------------------------------------- 代际安全 / 抢占

class TestGenerationSafety(Base):
    def test_preemption_after_expiry_and_old_holder_detected(self):
        old = LeaseLock(self.lock_path, holder_id="OLD", lease_duration=0.3)
        old.acquire(timeout=1.0)
        self.assertEqual(old.generation, 1)

        time.sleep(0.45)  # 租约到期，old 模拟被卡住（未续期）

        new = LeaseLock(self.lock_path, holder_id="NEW", lease_duration=5.0)
        self.assertTrue(new.try_acquire())
        self.assertEqual(new.generation, 2)

        # 旧持有者续期必须失败并主动放弃
        with self.assertRaises(PreemptedError):
            old.renew()
        self.assertFalse(old.is_held)
        self.assertFalse(old.validate())
        new.release()

    def test_stale_holder_write_rejected_by_fencing(self):
        """旧持有者被抢占后仍尝试写入资源 → 资源侧 fencing 拒绝。"""
        resource_path = os.path.join(self.dir, "resource.data")
        resource = FencedResource(resource_path)

        old = LeaseLock(self.lock_path, holder_id="OLD", lease_duration=0.3)
        old.acquire(timeout=1.0)
        resource.write(old, "old-gen1-write\n")  # gen=1 写入成功

        time.sleep(0.45)  # old 租约到期
        new = LeaseLock(self.lock_path, holder_id="NEW", lease_duration=5.0)
        new.acquire(timeout=2.0)
        resource.write(new, "new-gen2-write\n")  # gen=2 写入成功

        # 关键断言：旧持有者带着过期代际 gen=1 写入，必须被拒绝
        old.token = old.token or "stale"  # old.renew 已 abandon；直接模拟其携带旧代际写
        old.generation = 1
        with self.assertRaises(StaleGenerationError):
            resource.write(old, "STALE-WRITE\n")

        content = resource.read().decode()
        self.assertIn("old-gen1-write", content)
        self.assertIn("new-gen2-write", content)
        self.assertNotIn("STALE-WRITE", content)
        print("\n[fencing] 旧持有者 gen=1 写入被拒绝，资源内容未被污染")
        new.release()

    def test_renew_detects_deleted_file(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=5.0)
        lock.acquire(timeout=1.0)
        os.unlink(self.lock_path)  # 文件被删
        with self.assertRaises(LockLostError):
            lock.renew()
        self.assertFalse(lock.is_held)  # 主动放弃，不得静默继续

    def test_renew_detects_tampered_content(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=0.3)
        lock.acquire(timeout=1.0)

        # 场景 1：内容被替换为其他持有者（同代际不同 token）
        with open(self.lock_path) as f:
            rec = json.load(f)
        rec["token"] = "attacker-token"
        rec["holder"] = "attacker"
        with open(self.lock_path, "w") as f:
            json.dump(rec, f)
        with self.assertRaises(PreemptedError):
            lock.renew()
        self.assertFalse(lock.is_held)

        # 场景 2：文件被改写成无法解析的内容
        lock2 = LeaseLock(self.lock_path, holder_id="B", lease_duration=5.0)
        lock2.acquire(timeout=2.0)  # 攻击者记录租约仅 0.3s，过期后抢占
        with open(self.lock_path, "w") as f:
            f.write("not-json{{{")
        with self.assertRaises(LockLostError):
            lock2.renew()
        self.assertFalse(lock2.is_held)


# ---------------------------------------------------------------- 时钟漂移

class TestClockDrift(Base):
    def make_fake_lock(self, wall, mono, **kw):
        return LeaseLock(
            self.lock_path,
            holder_id="A",
            lease_duration=10.0,
            drift_tolerance=0.5,
            clock=lambda: wall[0],
            monotonic=lambda: mono[0],
            **kw,
        )

    def test_backward_clock_jump_abandons_lock(self):
        wall, mono = [1000.0], [500.0]
        lock = self.make_fake_lock(wall, mono)
        lock.acquire(timeout=1.0)
        wall[0] += 5.0
        mono[0] += 5.0
        lock.renew()  # 正常续期

        wall[0] -= 3.0   # 时钟回拨 3s
        mono[0] += 1.0
        with self.assertRaises(ClockDriftError):
            lock.renew()
        self.assertFalse(lock.is_held)  # 主动放弃
        print("\n[clock] 时钟回拨 3s → ClockDriftError，持有者已主动放弃")

    def test_forward_clock_jump_detected(self):
        wall, mono = [1000.0], [500.0]
        lock = self.make_fake_lock(wall, mono)
        lock.acquire(timeout=1.0)
        wall[0] += 120.0  # wall 大幅前跳，monotonic 只走了 1s
        mono[0] += 1.0
        with self.assertRaises(ClockDriftError):
            lock.renew()
        self.assertFalse(lock.is_held)

    def test_future_file_mtime_detected(self):
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=5.0,
                         drift_tolerance=0.2)
        lock.acquire(timeout=1.0)
        future = time.time() + 60.0
        os.utime(self.lock_path, (future, future))  # 文件 mtime 来自未来
        with self.assertRaises(ClockDriftError):
            lock.renew()
        self.assertFalse(lock.is_held)


# ---------------------------------------------------------------- 锁文件损坏

class TestCorruption(Base):
    def test_corrupt_lock_file_recovered_with_generation_continuity(self):
        lock1 = LeaseLock(self.lock_path, holder_id="A", lease_duration=0.3)
        lock1.acquire(timeout=1.0)
        self.assertEqual(lock1.generation, 1)

        # 模拟崩溃写坏锁文件，并把 mtime 拨到一个租约周期之前
        with open(self.lock_path, "wb") as f:
            f.write(b"\x00\x01garbage")
        old = time.time() - 10.0
        os.utime(self.lock_path, (old, old))

        lock2 = LeaseLock(self.lock_path, holder_id="B", lease_duration=0.3)
        self.assertTrue(lock2.try_acquire())
        self.assertEqual(lock2.generation, 2)  # 代际经由 sidecar 延续
        with self.assertRaises(LockLostError):
            lock1.renew()
        lock2.release()

    def test_fresh_corrupt_file_waits_one_lease_then_preempts(self):
        with open(self.lock_path, "w") as f:
            f.write("garbage")  # mtime 为现在
        lock = LeaseLock(self.lock_path, holder_id="A", lease_duration=0.3)
        self.assertFalse(lock.try_acquire())  # 保守等待，不立即抢占
        time.sleep(0.4)
        self.assertTrue(lock.try_acquire())   # 一个租约周期后可抢占
        lock.release()


# ---------------------------------------------------------------- 并发争抢

def _contention_worker(lock_path, worker_id, rounds, hold_time, queue):
    lock = LeaseLock(lock_path, holder_id=f"worker-{worker_id}", lease_duration=2.0)
    latencies = []
    for _ in range(rounds):
        t0 = time.monotonic()
        lock.acquire(timeout=120.0, retry_interval=0.005)
        latencies.append(time.monotonic() - t0)
        time.sleep(hold_time)
        lock.release()
    queue.put(latencies)


class TestContention(Base):
    def test_concurrent_contention_latency_distribution(self):
        workers, rounds, hold_time = 8, 5, 0.02
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        procs = [
            ctx.Process(target=_contention_worker,
                        args=(self.lock_path, i, rounds, hold_time, queue))
            for i in range(workers)
        ]
        t0 = time.monotonic()
        for p in procs:
            p.start()
        latencies = []
        for _ in procs:
            latencies.extend(queue.get())
        for p in procs:
            p.join(timeout=60)
            self.assertEqual(p.exitcode, 0)
        elapsed = time.monotonic() - t0

        self.assertEqual(len(latencies), workers * rounds)
        print(f"\n[contention] {workers} 进程 x {rounds} 轮, 持锁 {hold_time*1000:.0f}ms, "
              f"总耗时 {elapsed:.2f}s")
        print(f"[contention] 获取耗时分布: {describe_dist(latencies)}")
        # 粗放上界：任何一次获取都不应超过 总轮次*持锁时间 + 余量
        self.assertLess(max(latencies), workers * rounds * hold_time + 5.0)

    def test_threaded_mutual_exclusion_critical_section(self):
        """多线程争抢下临界区计数必须精确（验证互斥语义）。"""
        counter = {"n": 0}
        threads_n, increments = 6, 20

        def worker(tid):
            lock = LeaseLock(self.lock_path, holder_id=f"t{tid}", lease_duration=2.0)
            for _ in range(increments):
                lock.acquire(timeout=30.0, retry_interval=0.002)
                cur = counter["n"]          # 非原子读-改-写
                time.sleep(0.001)
                counter["n"] = cur + 1
                lock.release()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(counter["n"], threads_n * increments)
        print(f"\n[threads] {threads_n} 线程 x {increments} 次非原子自增 = {counter['n']} "
              f"(期望 {threads_n*increments})")


# ---------------------------------------------------------------- 强杀恢复

CHILD_PROGRAM = """
import sys, time
sys.path.insert(0, {here!r})
from lease_lock import LeaseLock
lock = LeaseLock(sys.argv[1], holder_id="victim", lease_duration=float(sys.argv[2]))
lock.acquire(timeout=10.0)
print("ACQUIRED {{:.6f}} {{}}".format(time.time(), lock.generation), flush=True)
time.sleep(3600)
""".format(here=HERE)


class TestKillRecovery(Base):
    def test_sigkill_recovery_timeline(self):
        lease = 0.8
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD_PROGRAM, self.lock_path, str(lease)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            line = proc.stdout.readline().strip()
            self.assertTrue(line.startswith("ACQUIRED"), msg=f"子进程输出异常: {line!r}")
            child_acquired_at = float(line.split()[1])
            child_gen = int(line.split()[2])
            proc.stdout.close()

            t_kill = time.time()
            proc.send_signal(signal.SIGKILL)  # 强杀，不给清理机会
            proc.wait(timeout=5)
            self.assertEqual(proc.returncode, -signal.SIGKILL)

            # 父进程轮询抢锁，记录恢复时间线
            parent = LeaseLock(self.lock_path, holder_id="parent", lease_duration=5.0)
            t_acquired = None
            while time.monotonic() - t_kill < lease + 5.0:
                if parent.try_acquire():
                    t_acquired = time.time()
                    break
                time.sleep(0.01)
            self.assertIsNotNone(t_acquired, "租约到期后仍无法获取锁")

            expiry_at = child_acquired_at + lease
            timeline = [
                ("子进程获取锁 (gen=%d)" % child_gen, child_acquired_at),
                ("子进程被 SIGKILL", t_kill),
                ("租约到期时刻(理论)", expiry_at),
                ("父进程获取锁 (gen=%d)" % parent.generation, t_acquired),
            ]
            base = timeline[0][1]
            print("\n[kill-recovery] 强杀恢复时间线 (lease=%.1fs):" % lease)
            for name, ts in timeline:
                print(f"  t+{(ts-base)*1000:8.1f}ms  {name}")
            print(f"  恢复延迟 = kill→重新获取 {(t_acquired-t_kill)*1000:.1f}ms "
                  f"(其中等待租约到期约 {(expiry_at-t_kill)*1000:.1f}ms)")

            self.assertEqual(parent.generation, child_gen + 1)
            # 必须等到租约到期之后才能获取（允许少量时钟/调度误差）
            self.assertGreaterEqual(t_acquired, expiry_at - 0.05)
            self.assertLessEqual(t_acquired, expiry_at + 2.0)
            parent.release()
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)
