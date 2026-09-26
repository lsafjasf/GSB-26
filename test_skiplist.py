"""Self-tests for skiplist.py: correctness, edge cases, reproducibility,
concurrency, and node recycling. Run: python3 test_skiplist.py -v
"""

import gc
import random
import threading
import unittest

from skiplist import SkipList, _Node


def live_node_objects():
    gc.collect()
    return sum(1 for o in gc.get_objects() if isinstance(o, _Node))


class TestBasicOps(unittest.TestCase):
    def test_empty_table(self):
        sl = SkipList(rng=random.Random(1))
        self.assertEqual(len(sl), 0)
        self.assertIsNone(sl.find(42))
        self.assertEqual(sl.find(42, "dflt"), "dflt")
        self.assertNotIn(42, sl)
        self.assertFalse(sl.delete(42))
        self.assertEqual(sl.range_scan(0, 100), [])
        self.assertEqual(sl.items(), [])
        self.assertEqual(sl.node_count(), 0)

    def test_single_element(self):
        sl = SkipList(rng=random.Random(1))
        self.assertTrue(sl.insert(7, "seven"))
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl.find(7), "seven")
        self.assertIsNone(sl.find(6))
        self.assertIsNone(sl.find(8))
        self.assertEqual(sl.range_scan(0, 100), [(7, "seven")])
        self.assertEqual(sl.range_scan(7, 7), [(7, "seven")])
        self.assertEqual(sl.range_scan(8, 100), [])
        self.assertTrue(sl.delete(7))
        self.assertEqual(len(sl), 0)
        self.assertIsNone(sl.find(7))
        self.assertFalse(sl.delete(7))

    def test_insert_find_delete_many(self):
        sl = SkipList(rng=random.Random(7))
        keys = list(range(2000))
        random.Random(99).shuffle(keys)
        for k in keys:
            self.assertTrue(sl.insert(k, k * 10))
        self.assertEqual(len(sl), 2000)
        for k in range(2000):
            self.assertEqual(sl.find(k), k * 10)
        self.assertIsNone(sl.find(2000))
        self.assertIsNone(sl.find(-1))
        for k in range(0, 2000, 2):
            self.assertTrue(sl.delete(k))
        for k in range(2000):
            if k % 2 == 0:
                self.assertIsNone(sl.find(k))
            else:
                self.assertEqual(sl.find(k), k * 10)
        self.assertEqual(len(sl), 1000)

    def test_duplicate_keys_overwrite(self):
        sl = SkipList(rng=random.Random(3))
        for i in range(10000):
            inserted = sl.insert(5, i)
            self.assertEqual(inserted, i == 0)
            self.assertEqual(len(sl), 1)
        self.assertEqual(sl.find(5), 9999)
        self.assertEqual(sl.items(), [(5, 9999)])
        self.assertTrue(sl.delete(5))
        self.assertEqual(len(sl), 0)

    def test_range_scan_sorted_and_bounded(self):
        sl = SkipList(rng=random.Random(11))
        model = {}
        rng = random.Random(12)
        for _ in range(5000):
            k = rng.randrange(500)
            if rng.random() < 0.6:
                sl.insert(k, k)
                model[k] = k
            else:
                sl.delete(k)
                model.pop(k, None)
        self.assertEqual(sl.items(), sorted(model.items()))
        for _ in range(200):
            lo = rng.randrange(500)
            hi = lo + rng.randrange(100)
            expect = [(k, v) for k, v in sorted(model.items()) if lo <= k <= hi]
            self.assertEqual(sl.range_scan(lo, hi), expect)


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_levels(self):
        def build(seed):
            sl = SkipList(p=0.25, rng=random.Random(seed))
            for k in range(5000):
                sl.insert(k, k)
            return sl.node_levels()

        a = build(1234)
        b = build(1234)
        c = build(4321)
        self.assertEqual(a, b, "same seed must reproduce identical level sequence")
        self.assertNotEqual(a, c, "different seeds should differ (overwhelmingly likely)")

    def test_level_sequence_matches_rng_stream(self):
        rng = random.Random(77)
        sl = SkipList(p=0.5, rng=rng)
        for k in range(100):
            sl.insert(k, k)
        rng2 = random.Random(77)
        sl2 = SkipList(p=0.5, rng=rng2)
        for k in range(100):
            sl2.insert(k, k)
        self.assertEqual(sl.node_levels(), sl2.node_levels())


class TestExtremeLevelDistributions(unittest.TestCase):
    def _exercise(self, sl):
        for k in range(1000):
            sl.insert(k, k * 2)
        for k in range(1000):
            self.assertEqual(sl.find(k), k * 2)
        self.assertEqual(len(sl), 1000)
        self.assertEqual(sl.range_scan(100, 199), [(k, k * 2) for k in range(100, 200)])
        for k in range(0, 1000, 3):
            self.assertTrue(sl.delete(k))
        self.assertEqual(len(sl), 1000 - 334)
        for k in range(1000):
            if k % 3 == 0:
                self.assertIsNone(sl.find(k))
            else:
                self.assertEqual(sl.find(k), k * 2)

    def test_all_level_one(self):
        sl = SkipList(level_source=lambda: 1)
        self._exercise(sl)
        self.assertTrue(all(lv == 1 for _, lv in sl.node_levels()))

    def test_all_max_level(self):
        sl = SkipList(max_level=16, level_source=lambda: 16)
        self._exercise(sl)
        self.assertTrue(all(lv == 16 for _, lv in sl.node_levels()))

    def test_level_source_capped_at_max(self):
        sl = SkipList(max_level=4, level_source=lambda: 1000)
        self._exercise(sl)
        self.assertTrue(all(lv == 4 for _, lv in sl.node_levels()))


class TestConcurrency(unittest.TestCase):
    """Multi-reader + single-writer. Readers must never observe a
    half-updated node: the invariant is value == key * 7 for every
    visible record."""

    BASE = 2000
    WRITER_KEYS = 4000

    def test_concurrent_read_write_scan(self):
        sl = SkipList(rng=random.Random(5))
        for k in range(self.BASE):
            sl.insert(k, k * 7)
        errors = []
        stop = threading.Event()

        def reader():
            rng = random.Random()
            try:
                while not stop.is_set():
                    k = rng.randrange(self.WRITER_KEYS + 1000)
                    v = sl.find(k)
                    if v is not None and v != k * 7:
                        errors.append(("find mismatch", k, v))
                    lo = rng.randrange(self.WRITER_KEYS)
                    hi = lo + rng.randrange(200)
                    rows = sl.range_scan(lo, hi)
                    keys = [k for k, _ in rows]
                    if keys != sorted(keys):
                        errors.append(("scan not sorted", rows[:5]))
                    if len(set(keys)) != len(keys):
                        errors.append(("scan duplicate", rows[:10]))
                    if any(k < lo or k > hi for k in keys):
                        errors.append(("scan out of range", lo, hi))
                    if any(v != k * 7 for k, v in rows):
                        errors.append(("scan half-updated", rows[:5]))
            except Exception as exc:  # noqa: BLE001
                errors.append(("reader exception", repr(exc)))

        def writer():
            for k in range(self.BASE, self.WRITER_KEYS):
                sl.insert(k, k * 7)
            for k in range(0, self.BASE, 2):
                sl.delete(k)
            for k in range(self.WRITER_KEYS, self.WRITER_KEYS + 500):
                sl.insert(k, k * 7)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        w = threading.Thread(target=writer)
        w.start()
        w.join()
        stop.set()
        for t in readers:
            t.join()

        self.assertEqual(errors, [])
        expect = [(k, k * 7) for k in range(self.WRITER_KEYS + 500)
                  if not (k < self.BASE and k % 2 == 0)]
        self.assertEqual(sl.items(), expect)


class TestNodeRecycling(unittest.TestCase):
    def test_nodes_recycled_after_100k_insert_delete(self):
        sl = SkipList(rng=random.Random(9))
        baseline = live_node_objects()

        rounds = 100_000
        for i in range(rounds):
            sl.insert(i, i)
            sl.delete(i)
        self.assertEqual(len(sl), 0)
        self.assertEqual(sl.node_count(), 0)
        after = live_node_objects()
        self.assertLessEqual(after - baseline, 10,
                             "nodes must be reclaimed after delete")

        for i in range(rounds):
            sl.insert(i, i)
        self.assertEqual(sl.node_count(), rounds)
        peak = live_node_objects()
        self.assertGreaterEqual(peak - baseline, rounds)
        for i in range(rounds):
            sl.delete(i)
        self.assertEqual(sl.node_count(), 0)
        after = live_node_objects()
        self.assertLessEqual(after - baseline, 10,
                             "node count must fall back after mass delete")
        print("\n[recycling] baseline=%d peak=%d after=%d (rounds=%d)"
              % (baseline, peak, after, rounds))


if __name__ == "__main__":
    unittest.main(verbosity=2)
