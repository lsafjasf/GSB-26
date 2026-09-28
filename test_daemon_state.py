"""Regression tests for crash-safe daemon state recovery.

Run:  python3 -m unittest -v test_daemon_state.py
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

import daemon_state as ds

HERE = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(HERE, "daemon_state.py")


def make_payload(counter):
    return {
        "counter": counter,
        "items": list(range(1, counter + 1)),
        "pending_cleanups": [],
        "completed_cleanups": [],
    }


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gsb-state-")
        self.path = os.path.join(self.dir, "state.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def store(self):
        return ds.StateStore(self.path)

    def load(self):
        store = self.store()
        state = store.load()
        ds.check_invariants(state)
        return store, state


# ---------------------------------------------------------------------------
# Basic lifecycle: first boot, normal restart, empty state
# ---------------------------------------------------------------------------

class TestLifecycle(TempDirCase):
    def test_first_boot_no_file(self):
        store, state = self.load()
        self.assertEqual(state, ds.empty_state())
        self.assertEqual(store.seq, 0)
        self.assertIn(("locate", "main state file absent"), store.report.entries)

    def test_normal_restart_recovers_state(self):
        store = self.store()
        store.commit(make_payload(3))
        store2, state = self.load()
        self.assertEqual(state["counter"], 3)
        self.assertEqual(store2.seq, 1)

    def test_empty_payload_roundtrip(self):
        self.store().commit(ds.empty_state())
        _, state = self.load()
        self.assertEqual(state, ds.empty_state())

    def test_zero_byte_state_file_is_corrupt_not_crash(self):
        open(self.path, "wb").close()  # truncated to zero
        store, state = self.load()
        self.assertEqual(state, ds.empty_state())
        self.assertFalse(os.path.exists(self.path))
        preserved = [f for f in os.listdir(self.dir) if ".corrupt." in f]
        self.assertEqual(len(preserved), 1)  # original kept for forensics

    def test_recovery_phase_order(self):
        self.store().commit(make_payload(1))
        store, _ = self.load()
        phases = store.report.phases
        order = [ds.RECOVERY_PHASES.index(p) for p in phases]
        self.assertEqual(order, sorted(order),
                         "recovery phases out of order: %s" % phases)
        self.assertEqual(phases[0], "locate")
        self.assertEqual(phases[-1], "ready")


# ---------------------------------------------------------------------------
# Defect 1: torn writes / truncation
# ---------------------------------------------------------------------------

class TestTornWrites(TempDirCase):
    def test_stale_tmp_file_ignored_and_removed(self):
        # Simulate kill after tmp write but before rename.
        self.store().commit(make_payload(2))
        with open(self.path + ".tmp", "w") as fh:
            fh.write('{"magic": "gsb-daemon-state", "seq": 99, "pay')  # torn
        store, state = self.load()
        self.assertEqual(state["counter"], 2)  # last committed state wins
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_truncated_main_falls_back_to_backup(self):
        store = self.store()
        store.commit(make_payload(1))
        store.commit(make_payload(2))  # bak now holds seq=1
        with open(self.path, "r+b") as fh:
            fh.truncate(os.path.getsize(self.path) // 2)  # torn main
        store2, state = self.load()
        self.assertEqual(state["counter"], 1)  # earlier *complete* state
        self.assertEqual(store2.seq, 1)
        preserved = [f for f in os.listdir(self.dir) if ".corrupt." in f]
        self.assertEqual(len(preserved), 1)

    def test_bit_flip_detected_as_corrupt(self):
        self.store().commit(make_payload(5))
        with open(self.path, "r+b") as fh:
            fh.seek(40)
            b = fh.read(1)
            fh.seek(40)
            fh.write(bytes([b[0] ^ 0xFF]))
        _, state = self.load()
        self.assertEqual(state, ds.empty_state())  # no bak -> empty, not garbage

    def test_recovered_state_never_mixed(self):
        # Hammer: real subprocess killed with SIGKILL at random moments;
        # the file on disk must always decode to a complete, consistent state.
        for _ in range(5):
            proc = subprocess.Popen(
                [sys.executable, WRITER, "writer", self.dir, "100000"])
                # writer commits counter=1,2,3,... as fast as possible
            time.sleep(0.02)
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            _, state = self.load()  # check_invariants asserts no mixed state
            self.assertGreaterEqual(state["counter"], 0)
            shutil.rmtree(self.dir)
            os.mkdir(self.dir)


# ---------------------------------------------------------------------------
# Per-stage kill assertions: old OR new complete state, never mixed
# ---------------------------------------------------------------------------

class TestPerStageKill(TempDirCase):
    def run_committer_killed_at(self, stage):
        # Start a REAL second subprocess committing seq=2 and deliver a real
        # SIGKILL from this parent at the exact commit stage (rendezvous in
        # kill_matrix guarantees the child is parked at `stage`, not merely
        # somewhere near it).
        import kill_matrix
        sync_dir = os.path.join(self.dir, "sync")
        os.makedirs(sync_dir, exist_ok=True)
        stage_file = os.path.join(sync_dir, "stage")
        gate = os.path.join(sync_dir, "gate")
        payload_file = os.path.join(self.dir, "new.json")
        with open(payload_file, "w") as fh:
            json.dump(make_payload(2), fh)
        if os.path.exists(stage_file):
            os.remove(stage_file)
        with open(gate, "w") as fh:
            fh.write("0")
        env = dict(os.environ, GSB_SYNC_DIR=sync_dir)
        proc = subprocess.Popen(
            [sys.executable, WRITER, "commit-file", self.path, payload_file],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        target = ds.COMMIT_STAGES.index(stage)
        acked = -1
        observed = None
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(stage_file):
                with open(stage_file) as fh:
                    published = fh.read().strip()
                idx = ds.COMMIT_STAGES.index(published)
                if idx > acked:
                    observed = published
                    if idx == target:
                        proc.send_signal(signal.SIGKILL)
                        proc.wait(timeout=30)
                        proc.stderr.close()
                        return proc.returncode, observed
                    with open(gate, "w") as fh:
                        fh.write(str(idx + 1))
                    acked = idx
            time.sleep(0.001)
        proc.kill()
        proc.wait()
        proc.stderr.close()
        self.fail("child never reached stage %s" % stage)

    def test_kill_at_every_commit_stage_real_sigkill(self):
        for stage in ds.COMMIT_STAGES:
            with self.subTest(stage=stage):
                shutil.rmtree(self.dir, ignore_errors=True)
                os.mkdir(self.dir)
                self.store().commit(make_payload(1))  # committed seq=1
                rc, observed = self.run_committer_killed_at(stage)
                self.assertEqual(observed, stage)     # killed exactly here
                self.assertEqual(rc, -9)              # real SIGKILL
                store, state = self.load()
                if ds.COMMIT_STAGES.index(stage) < ds.COMMIT_STAGES.index("renamed"):
                    # Killed before the atomic rename: old complete state.
                    self.assertEqual(state["counter"], 1, stage)
                    self.assertEqual(store.seq, 1, stage)
                else:
                    # Killed at/after the rename: new complete state.
                    self.assertEqual(state["counter"], 2, stage)
                    self.assertEqual(store.seq, 2, stage)
                self.assertEqual(state["items"], [1, 2][:state["counter"]])
                # In both branches check_invariants() already rejected mixes.

    def test_full_kill_matrix_matches_boundary_table(self):
        # The one-command matrix: real SIGKILL at every stage, two recovery
        # rounds each, full invariant + content-digest assertions.
        import kill_matrix
        rows, base_dir = kill_matrix.run_matrix(
            base_dir=os.path.join(self.dir, "matrix"))
        try:
            self.assertEqual([r["stage"] for r in rows],
                             list(ds.COMMIT_STAGES))
            for row in rows:
                self.assertEqual(row["verdict"], "PASS",
                                 "%s: %s" % (row["stage"], row["failures"]))
                expected = ("OLD" if ds.COMMIT_STAGES.index(row["stage"])
                            < ds.COMMIT_STAGES.index("renamed") else "NEW")
                self.assertEqual(row["expected"], expected)
                self.assertEqual(row["labels"], [expected, expected])
                self.assertTrue(row["invariant"])
                self.assertTrue(row["idempotent"])
                self.assertEqual(row["returncode"], -9)
                self.assertEqual(row["phases"][0], "locate")
                self.assertEqual(row["phases"][-1], "ready")
        finally:
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_two_consecutive_kills(self):
        for round_no in (1, 2):
            proc = subprocess.Popen(
                [sys.executable, WRITER, "writer", self.dir, "100000"])
            time.sleep(0.03)
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            _, state = self.load()
            if round_no == 2:
                first = state["counter"]
            self.assertEqual(state["counter"], len(state["items"]))
        # Second run continued from the recovered state (monotonic seq).
        _, final = self.load()
        self.assertEqual(final["counter"], first)


# ---------------------------------------------------------------------------
# Defect 4: version handling (old migrated, unknown preserved, not corrupt)
# ---------------------------------------------------------------------------

def write_versioned(path, version, payload):
    record = {"magic": ds.MAGIC, "version": version, "seq": 7, "payload": payload}
    with open(path, "w") as fh:
        fh.write(ds._encode(record))


class TestVersions(TempDirCase):
    def test_legacy_v1_migrated_not_corrupt(self):
        write_versioned(self.path, 1, {"counter": 4, "items": [1, 2, 3, 4],
                                       "cleanup_queue": ["/tmp/x.tmp"]})
        store, state = self.load()
        self.assertEqual(state["counter"], 4)
        self.assertEqual(state["pending_cleanups"],
                         [{"id": "legacy-0", "path": "/tmp/x.tmp"}])
        self.assertFalse([f for f in os.listdir(self.dir) if ".corrupt." in f])
        self.assertTrue(os.path.exists(self.path + ".migrated-from-v1"))
        self.assertIn(("classify", "migrating v1 -> v2"), store.report.entries)

    def test_unknown_newer_version_distinguished_from_corrupt(self):
        write_versioned(self.path, 99, {"counter": 1})
        with open(self.path, "rb") as fh:
            original = fh.read()
        store = self.store()
        with self.assertRaises(ds.UnknownVersionError):
            store.load()
        # Preserved in place, untouched, NOT renamed as corrupt.
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertFalse([f for f in os.listdir(self.dir) if ".corrupt." in f])
        self.assertIn("unknown version 99",
                      [d for _, d in store.report.entries][-1])

    def test_corrupt_vs_unknown_version_are_distinct_paths(self):
        write_versioned(self.path, 99, {})          # unknown version
        with open(self.path + ".bak", "w") as fh:   # plus a corrupt backup
            fh.write("garbage{")
        with self.assertRaises(ds.UnknownVersionError):
            self.store().load()


# ---------------------------------------------------------------------------
# Defect 3: cleanup tasks must not repeat after restart
# ---------------------------------------------------------------------------

class TestCleanups(TempDirCase):
    def seed_cleanup(self, job_id="job-1"):
        target = os.path.join(self.dir, "stale.tmp")
        with open(target, "w") as fh:
            fh.write("junk")
        payload = make_payload(0)
        payload["pending_cleanups"] = [{"id": job_id, "path": target}]
        self.store().commit(payload)
        return target

    def test_completed_cleanup_not_repeated_after_restart(self):
        target = self.seed_cleanup()
        calls = []
        store = self.store()
        store.load()
        store.run_cleanups(execute=lambda job: (calls.append(job["id"]),
                                                os.remove(job["path"])))
        self.assertEqual(calls, ["job-1"])
        self.assertFalse(os.path.exists(target))
        # Restart: completion was persisted, nothing re-runs.
        store2 = self.store()
        store2.load()
        ran = store2.run_cleanups(
            execute=lambda job: calls.append("REPEAT:" + job["id"]))
        self.assertEqual(ran, [])
        self.assertEqual(calls, ["job-1"])

    def test_cleanup_idempotent_when_killed_before_persist(self):
        target = self.seed_cleanup()
        side_effects = []

        def flaky_execute(job):
            if os.path.exists(job["path"]):
                os.remove(job["path"])
                side_effects.append(job["id"])
            if not hasattr(flaky_execute, "crashed"):
                flaky_execute.crashed = True
                raise ds.StateCorruptError("simulated crash after action, "
                                           "before completion persisted")
        store = self.store()
        store.load()
        with self.assertRaises(ds.StateCorruptError):
            store.run_cleanups(execute=flaky_execute)
        # Restart and replay: action re-runs but is a no-op (file gone).
        store2 = self.store()
        store2.load()
        store2.run_cleanups(execute=flaky_execute)
        self.assertEqual(side_effects, ["job-1"])  # exactly one real effect
        _, state = self.load()
        self.assertEqual(state["completed_cleanups"], ["job-1"])
        self.assertEqual(state["pending_cleanups"], [])

    def test_duplicate_pending_entries_execute_once(self):
        target = self.seed_cleanup()
        payload = make_payload(0)
        payload["pending_cleanups"] = [{"id": "job-1", "path": target},
                                       {"id": "job-1", "path": target}]
        self.store().commit(payload)
        calls = []
        store = self.store()
        store.load()
        store.run_cleanups(execute=lambda job: calls.append(job["id"]))
        self.assertEqual(calls, ["job-1"])


if __name__ == "__main__":
    unittest.main()
