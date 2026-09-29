"""leasereport 自测：事件日志、报告聚合、时间范围过滤、一致性对数、
开启报告不改变加解锁语义与租约时长、强杀后的回收记录。

运行: python3 -m unittest test_leasereport -v
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filelease import AcquireTimeout, FileLeaseLock
from leasereport import (build_report, check_consistency, read_events,
                         render_text)


class TestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="filelease-report-")
        self.lock_path = os.path.join(self.dir, "res.lock")
        self.log_path = self.lock_path + ".events"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_lock(self, holder="holder", ttl=5.0, **kw):
        return FileLeaseLock(self.lock_path, holder_id=holder, ttl=ttl, **kw)

    def events(self):
        return read_events(self.log_path)


class TestEventLog(TestBase):
    def test_acquire_renew_release_events_and_consistency(self):
        lock = self.make_lock("A", ttl=0.6, report=True)
        lease = lock.acquire(timeout=1.0)
        for _ in range(3):
            time.sleep(0.2)
            lease.renew()
        lease.release()

        kinds = [e["event"] for e in self.events()]
        self.assertEqual(kinds, ["acquire", "renew", "renew", "renew", "release"])

        ok, detail = check_consistency(self.lock_path)
        self.assertTrue(ok, detail)

        # 最后一条状态快照与锁文件逐字段一致（可对数）
        with open(self.lock_path) as fh:
            current = json.load(fh)
        last = [e for e in self.events() if "record" in e][-1]["record"]
        for key in ("state", "holder", "generation", "issued_at", "ttl",
                    "expires_at"):
            self.assertEqual(current[key], last[key])

    def test_report_disabled_by_default_no_file_no_side_effect(self):
        lock = self.make_lock("A", ttl=0.5)          # 默认 report=False
        lease = lock.acquire(timeout=1.0)
        lease.renew()
        lease.release()
        self.assertFalse(os.path.exists(self.log_path),
                         "默认关闭时不应产生事件文件")

        # 开启报告后锁文件结构与租约时长不变（语义不变）
        lock2 = self.make_lock("B", ttl=0.5, report=True)
        lease2 = lock2.acquire(timeout=1.0)
        with open(self.lock_path) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["ttl"], 0.5)
        self.assertAlmostEqual(rec["expires_at"] - rec["issued_at"], 0.5, places=3)
        self.assertEqual(set(rec), {"version", "state", "holder", "generation",
                                    "issued_at", "ttl", "expires_at"})
        lease2.release()

    def test_wait_time_and_timeout_recorded(self):
        lock_a = self.make_lock("A", ttl=5.0, report=True)
        lease_a = lock_a.acquire(timeout=1.0)

        lock_b = self.make_lock("B", ttl=5.0, report=True)
        with self.assertRaises(AcquireTimeout):
            lock_b.acquire(timeout=0.3)
        time.sleep(0.2)
        t0 = time.monotonic()
        lease_a.release()
        lease_b = lock_b.acquire(timeout=2.0)
        waited = time.monotonic() - t0
        lease_b.release()

        events = self.events()
        timeouts = [e for e in events if e["event"] == "acquire_timeout"]
        self.assertEqual(len(timeouts), 1)
        self.assertGreaterEqual(timeouts[0]["wait_ms"], 250)

        b_acquire = [e for e in events
                     if e["event"] == "acquire" and e["holder"] == "B"][0]
        self.assertGreaterEqual(b_acquire["wait_ms"] / 1000.0, waited - 0.05)
        self.assertIsNone(b_acquire["preempted"])     # 正常释放后获取，非抢占
        self.assertEqual(b_acquire["prev_generation"], 1)

    def test_renew_frequency_and_remaining_lease(self):
        lock = self.make_lock("A", ttl=0.5, report=True)
        lease = lock.acquire(timeout=1.0)
        time.sleep(0.2)
        lease.renew()
        time.sleep(0.2)
        lease.renew()
        lease.release()

        report = build_report(self.events())
        renew = report["renew"]
        self.assertEqual(renew["total"], 2)
        # 续期间隔 ~200ms；续期时剩余租约 ~300ms（ttl 0.5s - 已用 0.2s）
        self.assertAlmostEqual(renew["interval_ms"]["mean"], 200, delta=80)
        self.assertAlmostEqual(renew["remaining_ms"]["mean"], 300, delta=100)
        self.assertEqual(renew["per_lease"][0]["renew_count"], 2)

    def test_time_range_filter(self):
        lock = self.make_lock("A", ttl=0.3, report=True)
        lease = lock.acquire(timeout=1.0)
        lease.release()
        t_mid = time.time()
        time.sleep(0.05)
        lease2 = lock.acquire(timeout=1.0)
        lease2.release()

        all_events = self.events()
        first_half = read_events(self.log_path, end=t_mid)
        second_half = read_events(self.log_path, start=t_mid)
        self.assertEqual(len(first_half) + len(second_half), len(all_events))
        self.assertTrue(all(e["ts"] <= t_mid for e in first_half))
        self.assertTrue(all(e["ts"] >= t_mid for e in second_half))
        # 时间窗内的报告只含窗内事件
        report = build_report(second_half)
        self.assertEqual(report["event_counts"]["acquire"], 1)


class TestPreemptionReport(TestBase):
    def test_expired_holder_preemption_and_generation_change(self):
        lock_old = self.make_lock("OLD", ttl=0.3, report=True)
        lease_old = lock_old.acquire(timeout=1.0)
        gen_old = lease_old.generation
        time.sleep(0.5)                               # OLD 停滞，租约过期

        lock_new = self.make_lock("NEW", ttl=5.0, report=True)
        lease_new = lock_new.acquire(timeout=2.0)
        lease_new.release()

        report = build_report(self.events())
        self.assertEqual(report["reclamation"]["count"], 1)
        pre = report["preemptions"][0]
        self.assertEqual(pre["prev_holder"], "OLD")
        self.assertEqual(pre["prev_generation"], gen_old)
        self.assertEqual(pre["new_holder"], "NEW")
        self.assertEqual(pre["new_generation"], gen_old + 1)   # 代际单调递增
        # 回收延迟：过期到被抢占的间隔（此处约 0.2s = 0.5s 停滞 - 0.3s ttl）
        self.assertGreater(pre["reclaim_delay_ms"], 100)
        self.assertLess(pre["reclaim_delay_ms"], 1000)

        gens = [g["generation"] for g in report["generations"]]
        self.assertEqual(gens, sorted(gens))
        self.assertEqual(gens, [gen_old, gen_old + 1])

        ok, detail = check_consistency(self.lock_path)
        self.assertTrue(ok, detail)

    def test_sigkill_reclamation_recorded(self):
        """子进程持锁后被 kill -9：报告应记录异常回收，延迟 ≈ ttl。"""
        ttl = 1.0
        marker = os.path.join(self.dir, "marker")
        child_code = (
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from filelease import FileLeaseLock\n"
            "lease = FileLeaseLock(%r, holder_id='victim', ttl=%r,"
            " report=True).acquire(timeout=5)\n"
            "open(%r, 'w').write('ok')\n"
            "time.sleep(60)\n"
            % (os.path.dirname(os.path.abspath(__file__)), self.lock_path,
               ttl, marker))
        child = subprocess.Popen([sys.executable, "-c", child_code])
        try:
            deadline = time.time() + 10
            while not os.path.exists(marker):
                self.assertLess(time.time(), deadline, "子进程未能获取锁")
                time.sleep(0.02)
            child.send_signal(signal.SIGKILL)
            child.wait()
            self.assertEqual(child.returncode, -signal.SIGKILL)

            lock = self.make_lock("recoverer", ttl=5.0, report=True)
            lease = lock.acquire(timeout=ttl + 5)
            lease.release()
        finally:
            if child.poll() is None:
                child.kill()

        report = build_report(self.events())
        self.assertEqual(report["reclamation"]["count"], 1)
        pre = report["preemptions"][0]
        self.assertEqual(pre["prev_holder"], "victim")
        self.assertEqual(pre["new_holder"], "recoverer")
        # 强杀无释放：回收延迟 ≈ 0（一到期就被等着的 recoverer 抢走）
        self.assertLess(pre["reclaim_delay_ms"], 1500)
        self.assertGreaterEqual(pre["reclaim_delay_ms"], 0)
        # 跨进程事件在同一日志中，代际连续
        gens = [g["generation"] for g in report["generations"]]
        self.assertEqual(gens, [1, 2])

        ok, detail = check_consistency(self.lock_path)
        self.assertTrue(ok, detail)

        # 文本报告可渲染且包含关键小节
        text = render_text(report, lock_path=self.lock_path)
        for section in ("加锁等待时长", "续期频率与剩余租约",
                        "抢占事件与代际变化", "异常退出回收"):
            self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
