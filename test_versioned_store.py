"""Self-tests for versioned_store. Run: python3 -m unittest -v test_versioned_store"""

import random
import threading
import time
import unittest

from versioned_store import DELETE, VersionedStore, VersionNotFoundError


class TestBasicVersions(unittest.TestCase):
    def test_empty_store(self):
        s = VersionedStore()
        self.assertEqual(s.latest_version, 0)
        self.assertIsNone(s.get("missing"))
        self.assertEqual(s.get("missing", default="d"), "d")
        self.assertEqual(s.scan(), [])
        self.assertEqual(s.count(), 0)
        self.assertEqual(s.scan("a", "z", version=0), [])

    def test_single_version(self):
        s = VersionedStore()
        v = s.commit([("b", 2), ("a", 1), ("c", 3)])
        self.assertEqual(v, 1)
        self.assertEqual(s.get("a"), 1)
        self.assertEqual(s.scan(), [("a", 1), ("b", 2), ("c", 3)])
        self.assertEqual(s.count(), 3)

    def test_single_key_change_keeps_history(self):
        s = VersionedStore()
        v1 = s.commit([("a", 1), ("b", 2)])
        v2 = s.put("a", 10)
        self.assertEqual(s.get("a", v1), 1)   # old version intact
        self.assertEqual(s.get("a", v2), 10)  # new version updated
        self.assertEqual(s.get("b", v1), 2)
        self.assertEqual(s.get("b", v2), 2)   # untouched key shared
        self.assertEqual(s.scan(version=v1), [("a", 1), ("b", 2)])
        self.assertEqual(s.scan(version=v2), [("a", 10), ("b", 2)])

    def test_structural_sharing(self):
        # White-box: after changing one key, unchanged subtrees are the
        # exact same objects (no full copy).
        s = VersionedStore()
        v1 = s.commit((i, i) for i in range(1000))
        v2 = s.put(500, -1)
        r1, r2 = s._roots[v1], s._roots[v2]
        self.assertIsNot(r1, r2)                       # path copied

        def node_ids(root):
            ids, stack = set(), [root]
            while stack:
                n = stack.pop()
                if n is not None:
                    ids.add(id(n))
                    stack.extend((n.left, n.right))
            return ids

        old = node_ids(r1)
        shared = sum(1 for i in node_ids(r2) if i in old)
        # 1000 nodes total, only O(log n) nodes copied per write.
        self.assertGreaterEqual(shared, 990)

    def test_batch_update_atomic_view(self):
        s = VersionedStore()
        v1 = s.commit((f"k{i}", 0) for i in range(100))
        batch = [(f"k{i}", i) for i in range(100)]
        v2 = s.commit(batch)
        self.assertEqual(v2, v1 + 1)
        self.assertEqual(s.scan(version=v2), sorted(batch))
        self.assertEqual(s.scan(version=v1), sorted((f"k{i}", 0) for i in range(100)))

    def test_delete_and_read_history(self):
        s = VersionedStore()
        v1 = s.commit([("x", 1), ("y", 2), ("z", 3)])
        v2 = s.delete("y")
        v3 = s.commit([("x", DELETE), ("w", 4)])
        self.assertIsNone(s.get("y", v2))
        self.assertEqual(s.get("y", v1), 2)            # history still readable
        self.assertEqual(s.scan(version=v2), [("x", 1), ("z", 3)])
        self.assertEqual(s.scan(version=v3), [("w", 4), ("z", 3)])
        self.assertEqual(s.scan(version=v1), [("x", 1), ("y", 2), ("z", 3)])
        self.assertEqual(s.count(v1), 3)
        self.assertEqual(s.count(v3), 2)
        # deleting a missing key is a no-op but still creates a version
        v4 = s.delete("nope")
        self.assertEqual(s.scan(version=v4), s.scan(version=v3))

    def test_scan_bounds(self):
        s = VersionedStore()
        s.commit((i, i * 10) for i in range(20))
        self.assertEqual(s.scan(5, 9), [(i, i * 10) for i in range(5, 10)])
        self.assertEqual(s.scan(lo=15), [(i, i * 10) for i in range(15, 20)])
        self.assertEqual(s.scan(hi=2), [(i, i * 10) for i in range(3)])
        self.assertEqual(s.scan(100, 200), [])

    def test_randomized_against_reference(self):
        # 300 random versions cross-checked against a plain dict history.
        rng = random.Random(42)
        s = VersionedStore()
        ref = {}
        history = [dict(ref)]
        for _ in range(300):
            batch = {}
            for _ in range(rng.randint(1, 20)):
                k = rng.randint(0, 500)
                batch[k] = DELETE if rng.random() < 0.2 else rng.randint(0, 10**6)
            s.commit(batch.items())
            for k, v in batch.items():
                if v is DELETE:
                    ref.pop(k, None)
                else:
                    ref[k] = v
            history.append(dict(ref))
        for v in range(s.latest_version + 1):
            expected = sorted(history[v].items())
            self.assertEqual(s.scan(version=v), expected)
            for k, val in list(history[v].items())[:5]:
                self.assertEqual(s.get(k, v), val)


class TestVisibilityConsistency(unittest.TestCase):
    """Concurrent writer + readers: every scan must be a complete view of
    exactly one version, never a mix of two versions."""

    def test_concurrent_scan_never_mixes_versions(self):
        keys = [f"k{i}" for i in range(16)]
        s = VersionedStore()
        s.commit((k, 0) for k in keys)
        n_versions = 4000
        stop = threading.Event()
        errors = []
        barrier = threading.Barrier(4)

        def writer():
            barrier.wait()
            for v in range(1, n_versions + 1):
                # Each version sets ALL keys to v atomically: any scan that
                # observes two different values has mixed versions.
                s.commit([(k, v) for k in keys])
            stop.set()

        def reader():
            barrier.wait()
            try:
                while not stop.is_set():
                    # (a) scan the latest version
                    rows = s.scan()
                    self_check(rows)
                    # (b) scan a random committed version
                    v = random.randint(1, s.latest_version)
                    rows = s.scan(version=v)
                    if len(rows) != len(keys) or len({val for _, val in rows}) != 1:
                        errors.append(("mixed-scan", v, rows))
                        return
                    # (c) point reads at one version must agree
                    vals = {s.get(k, v) for k in keys}
                    if vals != {rows[0][1]}:
                        errors.append(("mixed-get", v, vals))
                        return
            except Exception as exc:  # noqa: BLE001
                errors.append(("exception", repr(exc)))

        def self_check(rows):
            if len(rows) != len(keys) or len({val for _, val in rows}) != 1:
                errors.append(("mixed-latest", rows))
                stop.set()

        threads = [threading.Thread(target=writer)] + [
            threading.Thread(target=reader) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(s.latest_version, n_versions + 1)
        # Final state: every key at the last written value.
        self.assertEqual({v for _, v in s.scan()}, {n_versions})

    def test_concurrent_batch_atomicity_with_deletes(self):
        # Versions alternate between "all 8 keys = v" and "only 4 keys = v".
        # A scan at any version must match one of the two shapes exactly.
        keys = [f"k{i}" for i in range(8)]
        s = VersionedStore()
        s.commit((k, 0) for k in keys)
        stop = threading.Event()
        errors = []

        def writer():
            for v in range(1, 3000):
                if v % 2:
                    s.commit([(k, v) for k in keys])
                else:
                    s.commit([(k, DELETE) for k in keys[:4]] + [(k, v) for k in keys[4:]])
            stop.set()

        def reader():
            while not stop.is_set():
                v = random.randint(1, s.latest_version)
                rows = s.scan(version=v)
                vals = {val for _, val in rows}
                # Either shape is fine (store version != writer's counter),
                # but it must be exactly one of the two shapes, uniformly.
                if not (len(rows) in (4, 8) and len(vals) == 1):
                    errors.append((v, rows))
                    return

        threads = [threading.Thread(target=writer)] + [
            threading.Thread(target=reader) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestGC(unittest.TestCase):
    def build(self, n_versions=50, keys_per_version=10):
        s = VersionedStore()
        ref_history = [{}]
        ref = {}
        for v in range(1, n_versions + 1):
            batch = [(f"k{v}_{i}", v * 100 + i) for i in range(keys_per_version)]
            s.commit(batch)
            ref.update(batch)
            ref_history.append(dict(ref))
        return s, ref_history

    def test_gc_by_version(self):
        s, history = self.build()
        dropped = s.gc(min_version=40)
        self.assertEqual(dropped, list(range(0, 40)))
        self.assertEqual(s.versions(), list(range(40, 51)))
        # Reclaimed versions raise a clear error, never partial data.
        for v in (0, 1, 39):
            with self.assertRaises(VersionNotFoundError):
                s.get("k1_0", v)
            with self.assertRaises(VersionNotFoundError):
                s.scan(version=v)
        # Retained versions remain fully correct.
        for v in range(40, 51):
            self.assertEqual(s.scan(version=v), sorted(history[v].items()))
            self.assertEqual(s.count(v), len(history[v]))

    def test_gc_by_time(self):
        s, history = self.build(n_versions=10)
        times = [s.version_time(v) for v in s.versions()]
        cutoff = times[5]  # versions with time < cutoff are dropped
        dropped = s.gc(before_time=cutoff)
        self.assertTrue(dropped)
        keep_from = max(dropped) + 1
        for v in dropped:
            with self.assertRaises(VersionNotFoundError):
                s.scan(version=v)
        for v in range(keep_from, 11):
            self.assertEqual(s.scan(version=v), sorted(history[v].items()))

    def test_gc_keeps_latest_even_if_all_match(self):
        s, history = self.build(n_versions=5)
        dropped = s.gc(before_time=time.time() + 1000)
        self.assertEqual(dropped, list(range(0, 5)))
        self.assertEqual(s.versions(), [5])
        self.assertEqual(s.scan(version=5), sorted(history[5].items()))

    def test_gc_then_keep_writing(self):
        s, history = self.build(n_versions=20)
        s.gc(min_version=15)
        v = s.put("after-gc", 1)
        self.assertEqual(v, 21)
        self.assertEqual(s.get("after-gc", v), 1)
        self.assertIsNone(s.get("after-gc", 20))
        self.assertEqual(s.get("k20_0", 20), 2000)
        with self.assertRaises(VersionNotFoundError):
            s.get("k1_0", 1)

    def test_gc_releases_memory(self):
        import tracemalloc

        tracemalloc.start()
        s = VersionedStore()
        for v in range(1, 2001):
            s.commit([(f"key{v}", v)])  # 2000 versions, 1 new key each
        before = tracemalloc.get_traced_memory()[0]
        s.gc(min_version=2000)
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        self.assertLess(after, before * 0.2)  # >80% of versioned memory freed
        self.assertEqual(s.count(), 2000)     # data itself fully intact


if __name__ == "__main__":
    unittest.main()
