"""Correctness, edge-case and reproducibility tests (stdlib unittest)."""

import random
import unittest

from skiplist import SkipList, random_level


class TestBasicOps(unittest.TestCase):
    def setUp(self):
        self.sl = SkipList(rand=random.Random(1234).random)

    def test_empty_list(self):
        self.assertEqual(len(self.sl), 0)
        self.assertIsNone(self.sl.get("missing"))
        self.assertEqual(self.sl.get("missing", "dflt"), "dflt")
        self.assertFalse(self.sl.delete("missing"))
        self.assertEqual(list(self.sl.scan()), [])
        self.assertEqual(list(self.sl.scan(1, 10)), [])

    def test_single_element(self):
        self.assertTrue(self.sl.insert(7, "seven"))
        self.assertEqual(len(self.sl), 1)
        self.assertEqual(self.sl.get(7), "seven")
        self.assertIn(7, self.sl)
        self.assertEqual(list(self.sl.scan()), [(7, "seven")])
        self.assertEqual(list(self.sl.scan(0, 100)), [(7, "seven")])
        self.assertEqual(list(self.sl.scan(8, 100)), [])
        self.assertEqual(list(self.sl.scan(0, 6)), [])
        self.assertTrue(self.sl.delete(7))
        self.assertEqual(len(self.sl), 0)
        self.assertIsNone(self.sl.get(7))

    def test_insert_get_delete_roundtrip(self):
        keys = list(range(2000))
        random.Random(7).shuffle(keys)
        for k in keys:
            self.assertTrue(self.sl.insert(k, k * 10))
        self.assertEqual(len(self.sl), 2000)
        for k in keys:
            self.assertEqual(self.sl.get(k), k * 10)
        random.Random(8).shuffle(keys)
        for i, k in enumerate(keys):
            self.assertTrue(self.sl.delete(k))
            self.assertIsNone(self.sl.get(k))
            self.assertEqual(len(self.sl), 2000 - i - 1)

    def test_many_duplicate_keys(self):
        for _ in range(5000):
            self.assertFalse(self.sl.insert(1, "x") if len(self.sl) else self.sl.insert(1, "x") and False)
        # simpler: re-insert same key many times
        sl = SkipList(rand=random.Random(5).random)
        self.assertTrue(sl.insert(1, "v0"))
        for i in range(1, 5000):
            self.assertFalse(sl.insert(1, f"v{i}"))  # overwrite, no new node
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl.get(1), "v4999")
        self.assertEqual(sl.node_levels(), sl.node_levels())  # single node
        self.assertEqual(len(sl.node_levels()), 1)

    def test_scan_ranges(self):
        for k in range(100):
            self.sl.insert(k, str(k))
        self.assertEqual([k for k, _ in self.sl.scan(10, 20)], list(range(10, 21)))
        self.assertEqual([k for k, _ in self.sl.scan()], list(range(100)))
        self.assertEqual([k for k, _ in self.sl.scan(hi=3)], [0, 1, 2, 3])
        self.assertEqual([k for k, _ in self.sl.scan(lo=97)], [97, 98, 99])
        self.assertEqual(list(self.sl.scan(50, 40)), [])  # empty range
        self.assertEqual(list(self.sl.scan(200, 300)), [])


class TestExtremeLevelDistributions(unittest.TestCase):
    def test_all_level_one_degrades_to_sorted_list(self):
        # rand() always >= p  => every node gets exactly level 1
        sl = SkipList(p=0.5, rand=lambda: 0.999999)
        for k in range(500):
            sl.insert(k, k)
        self.assertTrue(all(l == 1 for l in sl.node_levels()))
        self.assertEqual([k for k, _ in sl.scan()], list(range(500)))
        for k in range(0, 500, 2):
            self.assertTrue(sl.delete(k))
        self.assertEqual([k for k, _ in sl.scan()], list(range(1, 500, 2)))
        # lookup cost is linear in this degenerate case
        self.assertGreaterEqual(sl.search_steps(499), 200)

    def test_all_max_level(self):
        # rand() always < p  => every node reaches max_level
        sl = SkipList(p=0.5, max_level=16, rand=lambda: 0.0)
        for k in range(500):
            sl.insert(k, k)
        self.assertTrue(all(l == 16 for l in sl.node_levels()))
        self.assertEqual([k for k, _ in sl.scan(100, 105)], list(range(100, 106)))
        for k in range(500):
            self.assertEqual(sl.get(k), k)
        for k in range(500):
            self.assertTrue(sl.delete(k))
        self.assertEqual(len(sl), 0)


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_level_sequence(self):
        def build(seed):
            sl = SkipList(p=0.25, rand=random.Random(seed).random)
            for k in range(3000):
                sl.insert(k, k)
            return sl.node_levels()

        a, b = build(2026), build(2026)
        c = build(2027)
        self.assertEqual(a, b, "same seed must reproduce identical level sequence")
        self.assertNotEqual(a, c, "different seed should (overwhelmingly) differ")

    def test_level_draws_match_random_stream(self):
        # Levels must be derivable from the raw random stream alone.
        rng = random.Random(99)
        expected = [random_level(rng.random, 0.5, 32) for _ in range(100)]
        sl = SkipList(p=0.5, rand=random.Random(99).random)
        for k in range(100):
            sl.insert(k, k)
        self.assertEqual(sl.node_levels(), expected)

    def test_delete_insert_sequence_reproducible(self):
        def run(seed):
            sl = SkipList(rand=random.Random(seed).random)
            rng = random.Random(seed + 1)
            for i in range(2000):
                if rng.random() < 0.5:
                    sl.insert(i % 500, i)
                else:
                    sl.delete(i % 500)
            return list(sl.scan()), sl.node_levels()

        self.assertEqual(run(11), run(11))


if __name__ == "__main__":
    unittest.main(verbosity=2)
