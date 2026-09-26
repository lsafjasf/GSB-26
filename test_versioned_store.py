"""Tests for versioned_store. Run: python3 -m unittest test_versioned_store -v
or simply: python3 test_versioned_store.py
"""

import random
import threading
import time
import unittest

from versioned_store import VersionedStore, VersionNotFoundError


class TestBasic(unittest.TestCase):
    def test_empty_store(self):
        s = VersionedStore()
        self.assertIsNone(s.latest_version)
        self.assertEqual(list(s.scan()), [])
        self.assertEqual(list(s.scan(version=None, start=1, end=9)), [])
        with self.assertRaises(KeyError):
            s.get("missing")
        with self.assertRaises(VersionNotFoundError):
            s.get("k", version=42)  # version never existed
        with self.assertRaises(VersionNotFoundError):
            list(s.scan(version=42))

    def test_single_version(self):
        s = VersionedStore()
        v1 = s.commit({"b": 2, "a": 1, "c": 3})
        self.assertEqual(s.get("a", v1), 1)
        self.assertEqual(s.get("c"), 3)  # default = latest
        self.assertEqual(list(s.scan(v1)), [("a", 1), ("b", 2), ("c", 3)])
        with self.assertRaises(KeyError):
            s.get("z", v1)

    def test_single_key_update_shares_data(self):
        s = VersionedStore()
        n = 10_000
        v1 = s.commit({i: i for i in range(n)})
        v2 = s.put(5_000, -1)
        # old version untouched, new version sees the change
        self.assertEqual(s.get(5_000, v1), 5_000)
        self.assertEqual(s.get(5_000, v2), -1)
        self.assertEqual(s.get(4_999, v2), 4_999)
        # path copying: only O(log n) new nodes for one updated key
        created = s.info(v2)["nodes_created"]
        self.assertLess(created, 4 * n.bit_length() + 8)
        self.assertGreater(created, 0)
        # full scans of both versions differ in exactly one slot
        d1 = dict(s.scan(v1))
        d2 = dict(s.scan(v2))
        d2[5_000] = 5_000
        self.assertEqual(d1, d2)

    def test_batch_update(self):
        s = VersionedStore()
        v1 = s.commit({i: i for i in range(100)})
        batch = {i: i * 1000 for i in range(0, 100, 2)}  # 50 updates
        deletes = list(range(1, 100, 2))                 # 50 deletes
        v2 = s.commit(updates=batch, deletes=deletes)
        self.assertEqual(s.info(v2)["updates"], 100)
        self.assertEqual(len(list(s.scan(v1))), 100)
        self.assertEqual(len(list(s.scan(v2))), 50)
        self.assertEqual(s.get(50, v2), 50_000)
        self.assertEqual(s.get(50, v1), 50)
        with self.assertRaises(KeyError):
            s.get(51, v2)
        self.assertEqual(s.get(51, v1), 51)

    def test_delete_then_read_history(self):
        s = VersionedStore()
        v1 = s.commit({"x": 1, "y": 2, "z": 3})
        v2 = s.delete("y")
        v3 = s.commit(deletes=["x", "z"])
        self.assertEqual(s.get("y", v1), 2)
        with self.assertRaises(KeyError):
            s.get("y", v2)
        self.assertEqual(list(s.scan(v3)), [])
        self.assertEqual(list(s.scan(v1)), [("x", 1), ("y", 2), ("z", 3)])
        with self.assertRaises(KeyError):
            s.delete("y")  # already gone in latest

    def test_overwrite_same_key_many_versions(self):
        s = VersionedStore()
        versions = [s.put("k", i) for i in range(100)]
        for i, v in enumerate(versions):
            self.assertEqual(s.get("k", v), i)

    def test_scan_bounds_and_limit(self):
        s = VersionedStore()
        v1 = s.commit({i: i for i in range(100)})
        self.assertEqual([k for k, _ in s.scan(v1, 10, 20)], list(range(10, 20)))
        self.assertEqual([k for k, _ in s.scan(v1, start=95)], [95, 96, 97, 98, 99])
        self.assertEqual([k for k, _ in s.scan(v1, end=3)], [0, 1, 2])
        self.assertEqual(len(list(s.scan(v1, 10, 90, limit=5))), 5)
        self.assertEqual(list(s.scan(v1, 50, 50)), [])  # empty range
        self.assertEqual(list(s.scan(v1, 200, 300)), [])  # beyond all keys


class TestVisibilityConsistency(unittest.TestCase):
    """A range scan must observe exactly one version's complete view."""

    def test_iterator_is_a_stable_snapshot(self):
        s = VersionedStore()
        v1 = s.commit({i: i for i in range(1000)})
        it = s.scan(v1)
        v2 = s.commit({i: -i for i in range(1000)})
        s.commit(deletes=[0])
        self.assertEqual(dict(it), {i: i for i in range(1000)})
        self.assertEqual(dict(s.scan(v2)), {i: -i for i in range(1000)})
        self.assertEqual(len(list(s.scan())), 999)

    def test_concurrent_writer_scans_are_never_torn(self):
        """One writer commits many versions; several readers continuously
        scan random committed versions. Every scan and every point read must
        equal the model snapshot of exactly that version."""
        rng = random.Random(1234)
        s = VersionedStore()
        n_commits = 1500
        keyspace = 400
        models = {}       # version_id -> dict snapshot (written once, then read-only)
        committed = []    # version ids in commit order
        model = {}
        errors = []
        done = threading.Event()

        def writer():
            for _ in range(n_commits):
                key = rng.randrange(keyspace)
                if rng.random() < 0.25 and key in model:
                    vid = s.commit(deletes=[key])
                    model.pop(key, None)
                else:
                    val = rng.randrange(1 << 30)
                    vid = s.put(key, val)
                    model[key] = val
                models[vid] = dict(model)
                committed.append(vid)
            done.set()

        def reader(reader_rng):
            while not done.is_set() or len(committed) < n_commits:
                ids = committed[:]
                if not ids:
                    continue
                vid = ids[reader_rng.randrange(len(ids))]
                expected = models[vid]
                try:
                    got = dict(s.scan(version=vid))
                except VersionNotFoundError as e:
                    errors.append("scan reclaimed version: %r" % e)
                    continue
                if got != expected:
                    errors.append(
                        "torn scan at v%d: %d pairs, expected %d"
                        % (vid, len(got), len(expected))
                    )
                for key in reader_rng.sample(sorted(expected), min(8, len(expected))):
                    try:
                        if s.get(key, version=vid) != expected[key]:
                            errors.append("torn get at v%d key %d" % (vid, key))
                    except KeyError:
                        errors.append("missing key at v%d key %d" % (vid, key))

        readers = [
            threading.Thread(target=reader, args=(random.Random(i),))
            for i in range(4)
        ]
        w = threading.Thread(target=writer)
        for t in readers:
            t.start()
        w.start()
        w.join()
        done.set()
        for t in readers:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(s.version_count(), n_commits)
        # final sanity: latest version equals final model
        self.assertEqual(dict(s.scan()), model)


class TestGC(unittest.TestCase):
    def _build(self, n_versions=50, keyspace=200):
        rng = random.Random(7)
        s = VersionedStore()
        model = {}
        models = {}
        for i in range(n_versions):
            batch = {}
            for _ in range(5):
                key = rng.randrange(keyspace)
                val = rng.randrange(1 << 20)
                batch[key] = val
                model[key] = val
            vid = s.commit(batch, timestamp=1000.0 + i)
            models[vid] = dict(model)
        return s, models

    def test_gc_by_version(self):
        s, models = self._build()
        keep_from = 30
        reclaimed = s.gc(keep_from)
        self.assertEqual(reclaimed, keep_from - 1)
        # reclaimed versions raise a clear error, never partial data
        for vid in range(1, keep_from):
            with self.assertRaises(VersionNotFoundError):
                s.get(0, version=vid)
            with self.assertRaises(VersionNotFoundError):
                list(s.scan(version=vid))
        # retained versions remain completely correct
        for vid in range(keep_from, s.latest_version + 1):
            self.assertEqual(dict(s.scan(version=vid)), models[vid])

    def test_gc_by_time(self):
        s, models = self._build()
        # versions have timestamps 1000.0 + i for version i+1
        reclaimed = s.gc_before_time(1029.5)
        self.assertEqual(reclaimed, 29)          # v1..v29 gone
        self.assertEqual(min(s.version_ids()), 30)  # v30 (ts=1029.0) is the watermark
        with self.assertRaises(VersionNotFoundError):
            s.get(0, version=29)
        for vid in s.version_ids():
            self.assertEqual(dict(s.scan(version=vid)), models[vid])

    def test_gc_by_time_keeps_watermark_not_wallclock(self):
        # reclaiming "before t" must keep the newest version <= t so that
        # as-of-t queries still work
        s, models = self._build()
        s.gc_before_time(1010.0)
        self.assertEqual(min(s.version_ids()), 11)  # v11 has ts=1010.0
        self.assertEqual(dict(s.scan(version=11)), models[11])

    def test_gc_nothing_when_time_before_all_versions(self):
        s, models = self._build()
        self.assertEqual(s.gc_before_time(0.0), 0)
        self.assertEqual(s.version_count(), 50)

    def test_gc_down_to_latest(self):
        s, models = self._build()
        latest = s.latest_version
        s.gc(latest)
        self.assertEqual(s.version_ids(), [latest])
        self.assertEqual(dict(s.scan()), models[latest])
        with self.assertRaises(VersionNotFoundError):
            s.get(0, version=latest - 1)

    def test_gc_invalid_watermark(self):
        s, _ = self._build()
        with self.assertRaises(VersionNotFoundError):
            s.gc(9999)

    def test_gc_actually_frees_nodes(self):
        s, _ = self._build(n_versions=100)
        before = s.live_node_count()
        s.gc(s.latest_version)  # keep only latest
        after = s.live_node_count()
        self.assertLess(after, before)

    def test_error_message_distinguishes_reclaimed(self):
        s = VersionedStore()
        v1 = s.put("a", 1)
        s.put("b", 2)
        s.gc(2)
        with self.assertRaisesRegex(VersionNotFoundError, "reclaimed"):
            s.get("a", version=v1)
        with self.assertRaisesRegex(VersionNotFoundError, "does not exist"):
            s.get("a", version=12345)


if __name__ == "__main__":
    unittest.main(verbosity=2)
