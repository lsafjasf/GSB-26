"""
lock_inspector 自测套件：巡检判定、dry-run 安全性、清理后立即可加锁且代际连续。

运行：
    python3 -m unittest -v test_lock_inspector
    python3 test_lock_inspector.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lease_lock import LeaseLock
from lock_inspector import (
    ACTION_CLEANUP,
    ACTION_PREEMPT,
    ACTION_WAIT,
    execute_inspection,
    inspect_directory,
    inspect_path,
)


def dead_pid() -> int:
    """找一个本机一定不存在的 pid。"""
    try:
        with open("/proc/sys/kernel/pid_max") as f:
            pid = int(f.read().strip()) + 1
    except OSError:
        pid = 1 << 22
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pid += 1
        else:
            pid += 1


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lock_inspector_test_")
        self.lock_path = os.path.join(self.dir, "res.lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_orphan_lockfile(self, generation: int, expired: bool = False,
                             lease_duration: float = 0.2) -> dict:
        """手工落一份持有者进程已死的锁文件 + 代际 sidecar。"""
        now = time.time()
        renewed = now - (lease_duration + 1.0) if expired else now
        rec = {
            "holder": f"{os.uname().nodename}:{dead_pid()}:deadbeef",
            "token": "tok-orphan",
            "generation": generation,
            "lease_duration": lease_duration,
            "renewed_at": renewed,
            "expires_at": renewed + lease_duration,
        }
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        with open(self.lock_path + ".gen", "w", encoding="utf-8") as f:
            f.write(str(generation))
        return rec


class TestInspectPath(Base):
    def test_valid_lock_suggests_wait(self):
        lock = LeaseLock(
            self.lock_path,
            holder_id=f"{os.uname().nodename}:{os.getpid()}:aaaaaaaa",
            lease_duration=5.0,
        )
        lock.acquire(timeout=1.0)
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.category, "valid")
        self.assertEqual(insp.action, ACTION_WAIT)
        self.assertEqual(insp.evidence["content_check"], "ok")
        self.assertTrue(insp.evidence["pid_alive"])
        self.assertEqual(insp.evidence["generation"], 1)
        self.assertEqual(insp.evidence["sidecar_generation"], 1)
        lock.release()

    def test_expired_lease_suggests_preempt(self):
        lock = LeaseLock(
            self.lock_path,
            holder_id=f"{os.uname().nodename}:{os.getpid()}:bbbbbbbb",
            lease_duration=0.2,
        )
        lock.acquire(timeout=1.0)
        time.sleep(0.3)  # 租约到期但不 release，模拟崩溃残留
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.category, "expired")
        self.assertEqual(insp.action, ACTION_PREEMPT)
        self.assertTrue(insp.evidence["expired"])
        self.assertLess(insp.evidence["lease_remaining_sec"], 0)

    def test_orphan_lock_suggests_cleanup(self):
        self.make_orphan_lockfile(generation=4, expired=False, lease_duration=60.0)
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.category, "orphan")
        self.assertEqual(insp.action, ACTION_CLEANUP)
        self.assertFalse(insp.evidence["pid_alive"])
        self.assertFalse(insp.evidence["expired"])  # 未过期但持有者已死，仍建议清理

    def test_corrupt_fresh_file_suggests_wait(self):
        with open(self.lock_path, "w") as f:
            f.write("{not json")
        insp = inspect_path(self.lock_path, lease_duration=5.0)
        self.assertEqual(insp.category, "corrupt")
        self.assertEqual(insp.action, ACTION_WAIT)
        self.assertTrue(insp.evidence["content_problems"])

    def test_corrupt_old_file_suggests_cleanup(self):
        with open(self.lock_path, "w") as f:
            f.write("{not json")
        old = time.time() - 10.0
        os.utime(self.lock_path, (old, old))
        insp = inspect_path(self.lock_path, lease_duration=5.0)
        self.assertEqual(insp.category, "corrupt")
        self.assertEqual(insp.action, ACTION_CLEANUP)
        self.assertGreaterEqual(insp.evidence["mtime_age_sec"], 5.0)

    def test_missing_required_fields_is_corrupt(self):
        with open(self.lock_path, "w") as f:
            json.dump({"holder": "x", "token": "y"}, f)
        old = time.time() - 10.0
        os.utime(self.lock_path, (old, old))
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.category, "corrupt")
        self.assertIn("缺少必需字段", insp.evidence["content_problems"][0])


class TestExecute(Base):
    def test_execute_cleanup_then_acquire_immediately_with_continuous_generation(self):
        self.make_orphan_lockfile(generation=7, expired=False, lease_duration=60.0)
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.action, ACTION_CLEANUP)

        changed, message = execute_inspection(insp)
        self.assertTrue(changed, message)
        self.assertFalse(os.path.exists(self.lock_path))
        # 代际 sidecar 必须保留
        with open(self.lock_path + ".gen") as f:
            self.assertEqual(int(f.read().strip()), 7)

        # 清理后新的加锁立即成功，且代际连续（7 -> 8）
        lock = LeaseLock(self.lock_path, holder_id="newcomer", lease_duration=5.0)
        self.assertTrue(lock.try_acquire())
        self.assertEqual(lock.generation, 8)
        lock.release()

    def test_execute_preempt_expired_then_acquire(self):
        lock = LeaseLock(self.lock_path, holder_id="crashed", lease_duration=0.2)
        lock.acquire(timeout=1.0)
        gen_before = lock.generation
        time.sleep(0.3)
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.action, ACTION_PREEMPT)
        changed, _ = execute_inspection(insp)
        self.assertTrue(changed)
        newcomer = LeaseLock(self.lock_path, holder_id="newcomer", lease_duration=5.0)
        self.assertTrue(newcomer.try_acquire())
        self.assertEqual(newcomer.generation, gen_before + 1)
        newcomer.release()

    def test_execute_recheck_skips_when_state_changed(self):
        # 巡检时：损坏且过期 -> 建议清理
        with open(self.lock_path, "w") as f:
            f.write("garbage")
        old = time.time() - 10.0
        os.utime(self.lock_path, (old, old))
        insp = inspect_path(self.lock_path)
        self.assertEqual(insp.action, ACTION_CLEANUP)
        # 巡检与执行之间，锁被正常持有者重建（有效租约）
        lock = LeaseLock(self.lock_path, holder_id="revived", lease_duration=5.0)
        self.assertTrue(lock.try_acquire())
        changed, message = execute_inspection(insp)
        self.assertFalse(changed)
        self.assertIn("复检", message)
        self.assertTrue(os.path.exists(self.lock_path))  # 未被误删
        lock.release()

    def test_execute_wait_action_is_noop(self):
        lock = LeaseLock(self.lock_path, holder_id="alive", lease_duration=5.0)
        lock.acquire(timeout=1.0)
        insp = inspect_path(self.lock_path)
        changed, _ = execute_inspection(insp)
        self.assertFalse(changed)
        self.assertTrue(os.path.exists(self.lock_path))
        lock.release()


class TestInspectDirectory(Base):
    def test_directory_scan_classifies_each_lock_and_skips_sidecars(self):
        # 1) 有效锁（本进程持有）
        alive = LeaseLock(os.path.join(self.dir, "a.lock"),
                          holder_id=f"{os.uname().nodename}:{os.getpid()}:cccccccc",
                          lease_duration=5.0)
        alive.acquire(timeout=1.0)
        # 2) 过期锁
        expired = LeaseLock(os.path.join(self.dir, "b.lock"), holder_id="gone",
                            lease_duration=0.2)
        expired.acquire(timeout=1.0)
        time.sleep(0.3)
        # 3) 损坏锁（陈旧）
        corrupt_path = os.path.join(self.dir, "c.lock")
        with open(corrupt_path, "w") as f:
            f.write("###")
        old = time.time() - 10.0
        os.utime(corrupt_path, (old, old))
        # 4) 非锁文件应被跳过
        with open(os.path.join(self.dir, "notes.txt"), "w") as f:
            f.write("hello")

        inspections, skipped = inspect_directory(self.dir)
        by_path = {i.path: i for i in inspections}
        self.assertEqual(len(inspections), 3)
        self.assertEqual(by_path[alive.path].action, ACTION_WAIT)
        self.assertEqual(by_path[expired.path].action, ACTION_PREEMPT)
        self.assertEqual(by_path[corrupt_path].action, ACTION_CLEANUP)
        self.assertEqual(len(skipped), 1)
        self.assertTrue(skipped[0][0].endswith("notes.txt"))
        # 每个结论都带可核对的依据字段
        for insp in inspections:
            self.assertIn("mtime", insp.evidence)
            self.assertIn("sidecar_generation", insp.evidence)
            self.assertTrue(insp.reasons)
        alive.release()

    def test_dry_run_directory_scan_does_not_modify(self):
        self.make_orphan_lockfile(generation=3)
        before = set(os.listdir(self.dir))
        with open(self.lock_path, "rb") as f:
            content_before = f.read()
        inspections, _ = inspect_directory(self.dir)
        self.assertEqual(inspections[0].action, ACTION_CLEANUP)
        # 仅巡检（dry-run）：目录内容与锁文件内容均不变
        self.assertEqual(set(os.listdir(self.dir)), before)
        with open(self.lock_path, "rb") as f:
            self.assertEqual(f.read(), content_before)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"] + sys.argv[1:])
