"""Tests for wal_kv: idempotency, checkpoint/write concurrency, crash
injection at every stage, edge cases. Run: python3 test_wal_kv.py -v"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wal_kv import (WALKV, scan_segment, list_segment_ids, load_checkpoint,
                    _segment_name)


def key(i):
    return "k%04d" % i


def val(i):
    return "val-%d" % i


def fresh_dir():
    return tempfile.mkdtemp(prefix="walkv-test-")


# ---------------------------------------------------------------------------
# child process for crash-injection tests:  python3 test_wal_kv.py child <stage> <dir>
# ---------------------------------------------------------------------------

_KILL_AFTER = {
    "after_log_write": 25,        # kill during the 25th log append
    "checkpoint_tmp_written": 1,  # kill mid-checkpoint (tmp written, no rename)
    "checkpoint_renamed": 1,      # kill right after atomic rename
    "rotate_closed": 2,           # kill between closing old and opening new segment
    "rotate_opened": 2,           # kill right after new segment opened
}


def run_child(stage, directory):
    fired = [0]

    def hook(point):
        if point == stage:
            fired[0] += 1
            if fired[0] >= _KILL_AFTER[stage]:
                os._exit(9)  # simulate kill -9 / power loss

    store = WALKV(directory, max_segment_bytes=256, crash_hook=hook)
    marker = open(os.path.join(directory, "markers.log"), "ab")
    for i in range(200):
        store.set(key(i), val(i))
        # marker is written only AFTER the record is durable in the log
        marker.write(key(i).encode() + b"\n")
        marker.flush()
        os.fsync(marker.fileno())
        if i == 60:
            store.checkpoint()
    store.close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class IdempotencyTest(unittest.TestCase):
    def test_replay_twice_same_state(self):
        d = fresh_dir()
        try:
            s = WALKV(d, max_segment_bytes=128)
            for i in range(100):
                s.set(key(i), val(i))
            s.delete(key(5))
            s.set(key(7), "updated")
            s.close()

            # collect all log records
            records = []
            for sid in list_segment_ids(d):
                recs, _ = scan_segment(os.path.join(d, _segment_name(sid)))
                records.extend(recs)
            self.assertGreater(len(records), 0)

            # apply the same log twice to a fresh store -> identical state
            d2 = fresh_dir()
            try:
                a = WALKV(d2)
                for r in records:
                    a._replay_record(r)
                snap1 = a.items()
                for r in records:
                    a._replay_record(r)  # second full replay
                self.assertEqual(snap1, a.items())
                a.close()
            finally:
                shutil.rmtree(d2, ignore_errors=True)

            # two independent recoveries agree with each other
            b = WALKV(d)
            c = WALKV(d)
            self.assertEqual(b.items(), c.items())
            self.assertEqual(b.items(), snap1)
            # and re-running recovery on a live store changes nothing
            b._recover()
            self.assertEqual(b.items(), snap1)
            self.assertEqual(b.get(key(7)), "updated")
            self.assertIsNone(b.get(key(5)))
            b.close()
            c.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ConcurrencyTest(unittest.TestCase):
    def test_checkpoint_consistent_with_concurrent_writes(self):
        d = fresh_dir()
        try:
            violations = []

            def hook(point):
                if point != "checkpoint_renamed":
                    return
                # Invariant: replaying every log record with lsn <= cp.lsn
                # must reproduce cp.state exactly (no lost or partial writes).
                cp = load_checkpoint(d)
                state = {}
                for sid in list_segment_ids(d):
                    recs, _ = scan_segment(os.path.join(d, _segment_name(sid)))
                    for r in recs:
                        if r["lsn"] <= cp["lsn"]:
                            if r["op"] == "set":
                                state[r["key"]] = r["value"]
                            else:
                                state.pop(r["key"], None)
                if state != cp["state"]:
                    violations.append(cp["lsn"])

            # gc disabled so the invariant check can see all historical segments
            store = WALKV(d, max_segment_bytes=4096, crash_hook=hook,
                          gc_segments=False)
            n_threads, n_keys = 4, 150

            def writer(tid):
                for i in range(n_keys):
                    store.set("t%d-%04d" % (tid, i), "v%d-%d" % (tid, i))

            threads = [threading.Thread(target=writer, args=(t,))
                       for t in range(n_threads)]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                store.checkpoint()
                time.sleep(0.001)
            for t in threads:
                t.join()
            store.checkpoint()
            store.close()

            self.assertEqual(violations, [],
                             "checkpoint/state mismatch at LSNs: %s" % violations)

            # every write must be present after recovery: either it was inside
            # the last checkpoint, or it is replayed from the log afterwards
            expected = {"t%d-%04d" % (t, i): "v%d-%d" % (t, i)
                        for t in range(n_threads) for i in range(n_keys)}
            s2 = WALKV(d)
            self.assertEqual(s2.items(), expected)
            s2.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CrashRecoveryTest(unittest.TestCase):
    def test_kill_at_every_stage(self):
        for stage in sorted(_KILL_AFTER):
            with self.subTest(stage=stage):
                d = fresh_dir()
                try:
                    proc = subprocess.run(
                        [sys.executable, os.path.abspath(__file__),
                         "child", stage, d],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(proc.returncode, 9,
                                     "child should have been hard-killed")

                    marked = set()
                    mp = os.path.join(d, "markers.log")
                    if os.path.exists(mp):
                        with open(mp, "rb") as f:
                            marked = {ln.decode().strip()
                                      for ln in f if ln.strip()}

                    # recovery must succeed and be consistent
                    store = WALKV(d, max_segment_bytes=256)
                    state = store.items()
                    n = len(state)
                    # recovered keys form a contiguous prefix k0000..k(n-1)
                    self.assertEqual(state, {key(i): val(i) for i in range(n)})
                    # every write acknowledged before the kill survived
                    self.assertGreaterEqual(n, len(marked))
                    for m in marked:
                        self.assertEqual(state[m], val(int(m[1:])))

                    # store is fully functional after recovery
                    for i in range(n, n + 10):
                        store.set(key(i), val(i))
                    store.close()
                    s2 = WALKV(d, max_segment_bytes=256)
                    self.assertEqual(len(s2), n + 10)
                    s2.close()
                finally:
                    shutil.rmtree(d, ignore_errors=True)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_log(self):
        d = fresh_dir()
        try:
            s = WALKV(d)
            self.assertEqual(s.items(), {})
            s.set("a", "1")
            s.close()
            s2 = WALKV(d)
            self.assertEqual(s2.items(), {"a": "1"})
            s2.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_checkpoint_only(self):
        d = fresh_dir()
        try:
            s = WALKV(d)
            for i in range(50):
                s.set(key(i), val(i))
            s.checkpoint()
            s.close()
            for sid in list_segment_ids(d):  # simulate all segments removed
                os.unlink(os.path.join(d, _segment_name(sid)))
            s2 = WALKV(d)
            self.assertEqual(s2.items(), {key(i): val(i) for i in range(50)})
            s2.set("after", "recovery")
            s2.close()
            s3 = WALKV(d)
            self.assertEqual(s3.get("after"), "recovery")
            self.assertEqual(len(s3), 51)
            s3.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_corrupt_tail(self):
        d = fresh_dir()
        try:
            s = WALKV(d)
            for i in range(30):
                s.set(key(i), val(i))
            s.close()
            path = os.path.join(d, _segment_name(list_segment_ids(d)[-1]))
            with open(path, "ab") as f:  # garbage appended by a torn write
                f.write(b"\xde\xad\xbe\xef\x00\x01")
            s2 = WALKV(d)
            self.assertEqual(s2.items(), {key(i): val(i) for i in range(30)})
            s2.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_corrupt_middle(self):
        d = fresh_dir()
        try:
            s = WALKV(d)
            for i in range(60):
                s.set(key(i), val(i))
            s.close()
            path = os.path.join(d, _segment_name(list_segment_ids(d)[-1]))
            with open(path, "r+b") as f:
                data = bytearray(f.read())
                data[len(data) // 2] ^= 0xFF  # flip a byte mid-segment
                f.seek(0)
                f.write(data)
            s2 = WALKV(d)
            state = s2.items()
            n = len(state)
            self.assertGreater(n, 0)
            self.assertLess(n, 60)
            # valid prefix recovered, nothing after the corruption
            self.assertEqual(state, {key(i): val(i) for i in range(n)})
            # log is still appendable after truncation of the corrupt tail
            s2.set("new", "x")
            s2.close()
            s3 = WALKV(d)
            self.assertEqual(len(s3), n + 1)
            self.assertEqual(s3.get("new"), "x")
            s3.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PerfSmokeTest(unittest.TestCase):
    def test_replay_100k(self):
        d = fresh_dir()
        try:
            n = 100_000
            s = WALKV(d, max_segment_bytes=4 << 20, fsync=False)
            for i in range(n):
                s.set("key%08d" % i, "value-%d" % i)
            s.close()
            t0 = time.perf_counter()
            s2 = WALKV(d)
            replay_s = time.perf_counter() - t0
            self.assertEqual(len(s2), n)
            s2.close()
            print("\n[perf] replay %d records in %.3fs -> %.0f records/s"
                  % (n, replay_s, n / replay_s))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "child":
        run_child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
