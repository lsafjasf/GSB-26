#!/usr/bin/env python3
"""Regression tests for crash-safe daemon state recovery.

Reproduces the four production bug classes and pins the fixed behaviour:

  1. half-written state file (kill -9 mid-write, torn/truncated file)
  2. recovery executed in the wrong order (cleanup before dependencies)
  3. completed cleanup re-executed after restart
  4. old-version state file misjudged as corrupt

Plus staged kill -9 assertions (state is always a complete pre- or
post-write state, never mixed), first start, normal restart, two
consecutive kills, and empty state file.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(HERE, "daemon.py")

CRASH_STAGES = [
    "tmp_partial",     # killed while writing the tmp file (torn tmp)
    "tmp_written",     # killed after fsync(tmp), before rename
    "renamed",         # killed after atomic rename, before dir fsync
    "dir_fsynced",     # killed after dir fsync, before backup update
    "backup_written",  # killed after backup rename (save complete)
]


class DaemonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="daemon-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    # ------------------------------------------------------------- helpers
    def run_cli(self, *args, crash_at=None):
        env = dict(os.environ)
        if crash_at:
            env["DAEMON_CRASH_AT"] = crash_at
        return subprocess.run(
            [sys.executable, DAEMON, "--dir", self.dir, *args],
            capture_output=True, text=True, env=env)

    def run_ok(self, *args, crash_at=None):
        proc = self.run_cli(*args, crash_at=crash_at)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def run_killed(self, *args, crash_at):
        proc = self.run_cli(*args, crash_at=crash_at)
        self.assertNotEqual(proc.returncode, 0,
                            "expected the process to be hard-killed")
        return proc

    def dump(self):
        return self.run_ok("dump")

    def state_pair(self, result):
        state = result["state"]
        return (state["seq"], state["work_count"])

    def ledger_lines(self):
        path = os.path.join(self.dir, "cleanup.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def order_lines(self):
        path = os.path.join(self.dir, "recovery_order.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def quarantined(self, suffix):
        return [n for n in os.listdir(self.dir)
                if n.startswith("state.json" + suffix + "-")]

    def write_main(self, raw):
        with open(os.path.join(self.dir, "state.json"), "wb") as fh:
            fh.write(raw)

    # --------------------------------------- scenario: first start / restart
    def test_first_start_no_state_file(self):
        result = self.dump()
        self.assertEqual(result["source"], "fresh")
        self.assertEqual(self.state_pair(result), (0, 0))
        self.assertEqual(result["state"]["version"], 2)

    def test_normal_restart_recovers_exact_state(self):
        self.run_ok("work")
        self.run_ok("work")
        result = self.dump()
        self.assertEqual(result["source"], "primary")
        self.assertEqual(self.state_pair(result), (2, 2))

    # ------------------------- bug class 1: half-written / torn state file
    def test_kill_at_every_write_stage_never_mixed(self):
        for stage in CRASH_STAGES:
            with self.subTest(stage=stage):
                self.dir = tempfile.mkdtemp(prefix="daemon-test-")
                self.run_ok("work")                      # complete state (1,1)
                pre = self.dump()
                self.assertEqual(self.state_pair(pre), (1, 1))
                self.run_killed("work", crash_at=stage)  # killed mid-save
                post = self.dump()
                pair = self.state_pair(post)
                # either the complete pre-write state or the complete
                # post-write state -- never a mixture of the two
                self.assertIn(pair, [(1, 1), (2, 2)],
                              "mixed state after kill at %s" % stage)
                self.assertEqual(post["state"]["version"], 2)
                self.assertEqual(
                    post["state"]["seq"], post["state"]["work_count"],
                    "seq/work_count diverged: mixed state")
                self.assertIn(post["source"], ("primary", "backup"))

    def test_torn_tmp_file_is_ignored_and_removed(self):
        self.run_ok("work")
        self.run_killed("work", crash_at="tmp_partial")
        result = self.dump()
        self.assertEqual(self.state_pair(result), (1, 1))
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")
                     and n.startswith("state.json")]
        self.assertEqual(leftovers, [])

    def test_truncated_state_file_falls_back_to_backup(self):
        self.run_ok("work")                              # main=bak=(1,1)
        # kill after main rename but before backup update: main=(2,2), bak=(1,1)
        self.run_killed("work", crash_at="dir_fsynced")
        main = os.path.join(self.dir, "state.json")
        with open(main, "r+b") as fh:                    # external truncation
            fh.truncate(17)
        result = self.dump()
        self.assertEqual(result["source"], "backup")
        self.assertEqual(self.state_pair(result), (1, 1))  # earlier complete
        self.assertEqual(len(self.quarantined(".corrupt")), 1)
        self.assertEqual(self.quarantined(".unsupported"), [])

    def test_half_written_json_detected_as_corrupt(self):
        self.run_ok("work")
        good = open(os.path.join(self.dir, "state.json"), "rb").read()
        self.write_main(good[: len(good) // 2])          # torn JSON
        result = self.dump()
        self.assertEqual(result["source"], "backup")
        self.assertEqual(self.state_pair(result), (1, 1))
        self.assertEqual(len(self.quarantined(".corrupt")), 1)

    def test_checksum_mismatch_detected_as_corrupt(self):
        self.run_ok("work")
        raw = bytearray(open(os.path.join(self.dir, "state.json"), "rb").read())
        idx = raw.index(b'"work_count": 1')
        raw[idx + 15] = ord("9")                          # flip a digit
        self.write_main(bytes(raw))
        result = self.dump()
        self.assertEqual(result["source"], "backup")
        self.assertEqual(self.state_pair(result), (1, 1))

    def test_empty_state_file_is_corrupt_not_unknown_version(self):
        self.run_ok("work")
        self.write_main(b"")                              # 0-byte file
        result = self.dump()
        self.assertEqual(result["source"], "backup")
        self.assertEqual(len(self.quarantined(".corrupt")), 1)
        self.assertEqual(self.quarantined(".unsupported"), [])

    def test_empty_state_file_without_backup_starts_fresh(self):
        self.write_main(b"")
        result = self.dump()
        self.assertEqual(result["source"], "fresh-after-corrupt")
        self.assertEqual(self.state_pair(result), (0, 0))
        self.assertEqual(len(self.quarantined(".corrupt")), 1)

    def test_two_consecutive_kills_stay_consistent(self):
        self.run_ok("work")                               # (1,1)
        self.run_killed("work", crash_at="renamed")       # main=(2,2), bak=(1,1)
        first = self.dump()
        self.assertIn(self.state_pair(first), [(1, 1), (2, 2)])
        self.run_killed("work", crash_at="tmp_partial")   # torn tmp again
        second = self.dump()
        pair = self.state_pair(second)
        self.assertIn(pair, [(2, 2), (3, 3)])
        self.assertEqual(pair[0], pair[1], "mixed state after two kills")
        self.assertEqual(second["source"], "primary")

    # ------------------- bug class 4: version handling vs. corruption
    def test_old_version_file_is_migrated_not_discarded(self):
        legacy = {"version": 1, "seq": 5, "work_count": 5,
                  "pending_cleanups": []}
        self.write_main(json.dumps(legacy).encode())
        result = self.dump()
        self.assertEqual(result["source"], "primary")
        state = result["state"]
        self.assertEqual(state["version"], 2)
        self.assertEqual((state["seq"], state["work_count"]), (5, 5))
        self.assertEqual(state["done_cleanups"], [])
        self.assertEqual(self.quarantined(".corrupt"), [])

    def test_unknown_version_is_distinct_from_corrupt_and_preserved(self):
        future = {"version": 999, "seq": 7, "work_count": 7,
                  "pending_cleanups": [], "done_cleanups": []}
        payload = json.dumps(future).encode()
        self.write_main(payload)
        result = self.dump()
        self.assertEqual(result["source"], "unsupported")
        self.assertEqual(self.state_pair(result), (0, 0))
        self.assertEqual(self.quarantined(".corrupt"), [])
        kept = self.quarantined(".unsupported")
        self.assertEqual(len(kept), 1)
        with open(os.path.join(self.dir, kept[0]), "rb") as fh:
            self.assertEqual(fh.read(), payload)          # original bytes kept
        self.assertFalse(os.path.exists(os.path.join(self.dir, "state.json")))

    # ------------------- bug class 3: cleanup must not repeat after restart
    def test_completed_cleanup_not_repeated_after_restart(self):
        self.run_ok("schedule-cleanup", "t1")
        first = self.dump()                               # runs cleanup t1
        self.assertEqual(first["state"]["done_cleanups"], ["t1"])
        self.assertEqual(first["state"]["pending_cleanups"], [])
        self.assertEqual(self.ledger_lines(), ["removed t1"])
        self.dump()                                       # restart again
        self.dump()
        self.assertEqual(self.ledger_lines(), ["removed t1"],
                         "cleanup re-executed after restart")

    def test_cleanup_kill_between_execute_and_checkpoint_is_idempotent(self):
        self.run_ok("schedule-cleanup", "t1")
        # kill after the junk file was deleted but before the checkpoint save
        self.run_killed("dump", crash_at="cleanup_executed")
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, "junk", "t1.tmp")))
        result = self.dump()                              # restart: retry
        self.assertEqual(self.ledger_lines(), ["removed t1"],
                         "cleanup had a visible side effect twice")
        self.assertEqual(result["state"]["done_cleanups"], ["t1"])
        self.assertEqual(result["state"]["pending_cleanups"], [])

    def test_kill_between_two_cleanups_resumes_with_next(self):
        self.run_ok("schedule-cleanup", "t1")
        self.run_ok("schedule-cleanup", "t2")
        self.run_killed("dump", crash_at="cleanup_executed")  # dies in t1
        result = self.dump()
        self.assertEqual(sorted(result["state"]["done_cleanups"]), ["t1", "t2"])
        self.assertEqual(sorted(self.ledger_lines()),
                         ["removed t1", "removed t2"])

    # ------------------- bug class 2: recovery order (deps before cleanup)
    def test_recovery_order_load_then_deps_then_cleanup(self):
        self.run_ok("schedule-cleanup", "t1")
        os.remove(os.path.join(self.dir, "recovery_order.log"))
        self.dump()
        self.assertEqual(self.order_lines(),
                         ["load", "deps", "cleanup:t1"])

    def test_recovery_is_idempotent_as_a_whole(self):
        self.run_ok("work")
        self.run_ok("schedule-cleanup", "t1")
        first = self.dump()
        second = self.dump()
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(self.ledger_lines(), ["removed t1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
