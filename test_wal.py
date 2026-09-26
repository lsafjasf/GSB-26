"""Tests for wal.KVStore: idempotency, concurrency, kill -9 injection,
edge cases (empty log, checkpoint-only, corrupt segment)."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

from wal import KVStore, SEG_SUFFIX

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "crash_worker.py")


class WalTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="waltest-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def seg_files(self):
        return sorted(f for f in os.listdir(self.dir) if f.endswith(SEG_SUFFIX))


class TestBasicAndEdgeCases(WalTestBase):
    def test_empty_log(self):
        # Recovery from a completely empty directory.
        store = KVStore.open(self.dir)
        self.assertEqual(store.snapshot(), {})
        store.set("a", 1)
        store.close()
        store = KVStore.open(self.dir)
        self.assertEqual(store.snapshot(), {"a": 1})
        store.close()

    def test_checkpoint_only(self):
        # After a checkpoint the old segments are gone; recovery must work
        # from the checkpoint alone.
        store = KVStore.open(self.dir)
        expected = {"k%d" % i: i for i in range(50)}
        for k, v in expected.items():
            store.set(k, v)
        store.checkpoint()
        store.close()
        # Even with every log segment deleted, state survives via checkpoint.
        for f in self.seg_files():
            os.remove(os.path.join(self.dir, f))
        store = KVStore.open(self.dir)
        self.assertEqual(store.snapshot(), expected)
        store.close()

    def test_checkpoint_rotation_deletes_old_segments(self):
        store = KVStore.open(self.dir, max_segment_bytes=256)
        for i in range(100):
            store.set("k%d" % i, i)
        self.assertGreater(len(self.seg_files()), 1)  # rotation happened
        store.checkpoint()
        self.assertEqual(len(self.seg_files()), 1)    # old segments deleted
        store.close()
        store = KVStore.open(self.dir)
        self.assertEqual(len(store), 100)
        store.close()

    def test_corrupt_segment_tail(self):
        store = KVStore.open(self.dir, fsync=True)
        for i in range(100):
            store.set("k%d" % i, i)
        store.close()
        path = os.path.join(self.dir, self.seg_files()[-1])
        size = os.path.getsize(path)
        with open(path, "r+b") as f:  # garbage in the middle of the file
            f.seek(size // 2)
            f.write(b"\xff\xfe garbage \x00" * 4)
        store = KVStore.open(self.dir)
        state = store.snapshot()
        self.assertGreater(len(state), 0)
        self.assertLess(len(state), 100)  # records after corruption are lost
        # Values before the corruption point are intact and contiguous.
        for i in range(len(state)):
            self.assertEqual(state["k%d" % i], i)
        # Store keeps accepting writes after truncating the torn tail.
        store.set("after", "crash")
        store.close()
        store = KVStore.open(self.dir)
        self.assertEqual(store.get("after"), "crash")
        self.assertEqual(len(store), len(state) + 1)
        store.close()


class TestIdempotentReplay(WalTestBase):
    def test_replay_twice_same_state(self):
        store = KVStore.open(self.dir, max_segment_bytes=512)
        for i in range(200):
            store.set("k%d" % i, i)
        store.checkpoint()
        for i in range(200, 300):
            store.set("k%d" % i, i)
        store.close()
        first = KVStore.open(self.dir).snapshot()
        second = KVStore.open(self.dir).snapshot()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 300)

    def test_duplicated_log_records_are_skipped(self):
        # Simulate replaying the same log twice: concatenate a copy of every
        # segment's records into an extra segment with a higher id.
        store = KVStore.open(self.dir, max_segment_bytes=512)
        for i in range(100):
            store.set("k%d" % i, i)
        store.close()
        expected = KVStore.open(self.dir).snapshot()
        records = b""
        for f in self.seg_files():
            with open(os.path.join(self.dir, f), "rb") as fh:
                records += fh.read()
        dup_id = max(int(f[: -len(SEG_SUFFIX)]) for f in self.seg_files()) + 1
        with open(os.path.join(self.dir, "%06d%s" % (dup_id, SEG_SUFFIX)),
                  "wb") as fh:
            fh.write(records)  # same lsns replayed a second time
        store = KVStore.open(self.dir)
        self.assertEqual(store.snapshot(), expected)
        store.close()


class TestConcurrentCheckpoint(WalTestBase):
    def test_checkpoint_and_writes_are_consistent(self):
        for _ in range(3):  # a few rounds to vary the interleaving
            shutil.rmtree(self.dir, ignore_errors=True)
            os.makedirs(self.dir)
            store = KVStore.open(self.dir, max_segment_bytes=4096, fsync=True)
            n_writers, n_keys = 4, 250
            expected = {"w%d/k%d" % (w, i): i
                        for w in range(n_writers) for i in range(n_keys)}
            errors = []

            def writer(w):
                try:
                    for i in range(n_keys):
                        store.set("w%d/k%d" % (w, i), i)
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(w,))
                       for w in range(n_writers)]
            for t in threads:
                t.start()
            # Checkpoint concurrently the whole time writers are running.
            while any(t.is_alive() for t in threads):
                store.checkpoint()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            store.close()
            # Every write is either in a checkpoint or replayed from the log.
            recovered = KVStore.open(self.dir)
            self.assertEqual(recovered.snapshot(), expected)
            recovered.close()


class TestKill9Injection(WalTestBase):
    def run_worker(self, phase):
        proc = subprocess.run(
            [sys.executable, WORKER, phase, self.dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, -signal.SIGKILL,
                         "worker should die by SIGKILL: %s" % proc.stderr)

    def test_kill_during_log_write(self):
        self.run_worker("log")
        store = KVStore.open(self.dir)
        state = store.snapshot()
        # 50 records were committed+fsynced; record 51 was a torn write.
        self.assertEqual(len(state), 50)
        for i in range(50):
            self.assertEqual(state["k%d" % i], i)
        store.close()

    def test_kill_during_checkpoint_write(self):
        self.run_worker("checkpoint")
        # checkpoint.tmp must be ignored; old checkpoint + log replay win.
        self.assertIn("checkpoint.tmp", os.listdir(self.dir))
        store = KVStore.open(self.dir)
        state = store.snapshot()
        self.assertEqual(len(state), 100)
        for i in range(100):
            self.assertEqual(state["k%d" % i], i)
        # And the store is fully usable afterwards.
        store.checkpoint()
        store.set("post", "recovery")
        store.close()
        self.assertEqual(KVStore.open(self.dir).get("post"), "recovery")

    def test_kill_during_rotation(self):
        self.run_worker("rotate")
        with open(os.path.join(self.dir, "expected.txt")) as f:
            n = int(f.read())
        self.assertGreater(n, 0)
        store = KVStore.open(self.dir)
        state = store.snapshot()
        self.assertEqual(len(state), n)
        for i in range(n):
            self.assertEqual(state["k%d" % i], i)
        store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
