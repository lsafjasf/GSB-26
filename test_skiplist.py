"""Self-tests for skiplist.py: correctness, edge cases, reproducibility,
rank queries, snapshot iterators, concurrency, and node recycling.
Run: python3 test_skiplist.py -v
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
        self.assertEqual(sl.range_scan(0, 100).to_list(), [])
        self.assertEqual(list(sl.range_scan(0, 100)), [])
        self.assertEqual(sl.items(), [])
        self.assertEqual(sl.node_count(), 0)
        self.assertIsNone(sl.rank(42))
        self.assertIsNone(sl.select(1))

    def test_single_element(self):
        sl = SkipList(rng=random.Random(1))
        self.assertTrue(sl.insert(7, "seven"))
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl.find(7), "seven")
        self.assertIsNone(sl.find(6))
        self.assertIsNone(sl.find(8))
        self.assertEqual(sl.range_scan(0, 100).to_list(), [(7, "seven")])
        self.assertEqual(sl.range_scan(7, 7).to_list(), [(7, "seven")])
        self.assertEqual(sl.range_scan(8, 100).to_list(), [])
        self.assertEqual(sl.rank(7), 1)
        self.assertEqual(sl.select(1), (7, "seven"))
        self.assertIsNone(sl.select(2))
        self.assertTrue(sl.delete(7))
        self.assertEqual(len(sl), 0)
        self.assertIsNone(sl.find(7))
        self.assertIsNone(sl.rank(7))
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
            self.assertEqual(sl.range_scan(lo, hi).to_list(), expect)


class TestRankQueries(unittest.TestCase):
    """select / rank must agree with positional indexing into an
    ordered scan, under random operation mixes and extreme layouts."""

    def _check_against_model(self, sl, model):
        items = sorted(model.items())
        n = len(items)
        self.assertEqual(len(sl), n)
        # every rank position
        for k in range(1, n + 1):
            self.assertEqual(sl.select(k), items[k - 1])
        # out-of-range selects
        self.assertIsNone(sl.select(0))
        self.assertIsNone(sl.select(-3))
        self.assertIsNone(sl.select(n + 1))
        self.assertIsNone(sl.select(n + 1000))
        # rank of every live key, and of absent keys
        for pos, (key, _) in enumerate(items, start=1):
            self.assertEqual(sl.rank(key), pos)
        self.assertIsNone(sl.rank(-1))
        self.assertIsNone(sl.rank(10 ** 9))
        # rank/select consistent with a full ordered snapshot
        snap = sl.range_scan(-10 ** 9, 10 ** 9).to_list()
        self.assertEqual(snap, items)
        for pos, (key, _) in enumerate(snap, start=1):
            self.assertEqual(sl.select(pos), (key, sl.find(key)))
            self.assertEqual(sl.rank(key), pos)

    def test_rank_select_vs_sorted_model(self):
        for seed in range(5):
            sl = SkipList(p=0.25, rng=random.Random(seed))
            model = {}
            rng = random.Random(1000 + seed)
            for _ in range(4000):
                k = rng.randrange(600)
                if rng.random() < 0.55:
                    sl.insert(k, k * 11)
                    model[k] = k * 11
                else:
                    sl.delete(k)
                    model.pop(k, None)
            self._check_against_model(sl, model)

    def test_rank_select_extreme_layouts(self):
        for source in (lambda: 1, lambda: 16):
            sl = SkipList(max_level=16, level_source=source)
            model = {}
            for k in range(1500):
                sl.insert(k, k)
                model[k] = k
            self._check_against_model(sl, model)
            for k in range(0, 1500, 2):
                sl.delete(k)
                del model[k]
            self._check_against_model(sl, model)

    def test_rank_select_after_mass_delete_reinsert(self):
        sl = SkipList(rng=random.Random(31))
        for k in range(3000):
            sl.insert(k, k)
        for k in range(3000):
            sl.delete(k)
        self.assertIsNone(sl.select(1))
        self.assertIsNone(sl.rank(0))
        for k in range(3000, 6000):
            sl.insert(k, k * 2)
        model = {k: k * 2 for k in range(3000, 6000)}
        self._check_against_model(sl, model)


class TestSnapshotIterator(unittest.TestCase):
    def test_snapshot_isolated_from_later_writes(self):
        sl = SkipList(rng=random.Random(41))
        for k in range(1000):
            sl.insert(k, k * 7)
        snap = sl.range_scan(100, 899)
        expect = [(k, k * 7) for k in range(100, 900)]
        self.assertEqual(len(snap), 800)
        # heavy mutation after the snapshot point
        for k in range(0, 500):
            sl.delete(k)              # delete snapshot-live keys
        for k in range(1000, 1200):
            sl.insert(k, k * 7)       # insert new keys
        for k in range(600, 900):
            sl.insert(k, -1)          # overwrite snapshot-live values
        # the snapshot is frozen at creation time
        self.assertEqual(snap.to_list(), expect)
        self.assertEqual(list(snap), expect)
        self.assertEqual(snap.remaining(), 0)
        self.assertRaises(StopIteration, next, snap)
        # while the live structure reflects the mutations
        self.assertEqual(sl.find(600), -1)
        self.assertIsNone(sl.find(100))
        self.assertEqual(sl.find(1100), 1100 * 7)

    def test_snapshot_is_reiterable_via_to_list_but_single_pass(self):
        sl = SkipList(rng=random.Random(43))
        for k in range(100):
            sl.insert(k, k)
        snap = sl.range_scan(10, 19)
        first = list(snap)
        self.assertEqual(first, [(k, k) for k in range(10, 20)])
        self.assertEqual(list(snap), [])          # already consumed
        self.assertEqual(snap.to_list(), first)   # buffer still inspectable

    def test_snapshot_does_not_pin_nodes(self):
        sl = SkipList(rng=random.Random(47))
        baseline = live_node_objects()
        for k in range(20000):
            sl.insert(k, k)
        snaps = [sl.range_scan(0, 19999) for _ in range(5)]
        for k in range(20000):
            sl.delete(k)
        self.assertEqual(sl.node_count(), 0)
        gc.collect()
        # snapshots hold (key, value) pairs, not nodes: deleted nodes
        # are reclaimed even while snapshots are still alive.
        self.assertLessEqual(live_node_objects() - baseline, 10)
        self.assertEqual(snaps[0].to_list(), [(k, k) for k in range(20000)])

    def test_snapshot_under_write_pressure(self):
        """Concurrent writer hammering the structure while snapshots are
        taken and consumed: every snapshot must be sorted, deduplicated,
        in-range, value-consistent, and fully consumable."""
        sl = SkipList(rng=random.Random(53))
        for k in range(3000):
            sl.insert(k, k * 7)
        errors = []
        stop = threading.Event()

        def writer(seed):
            rng = random.Random(seed)
            while not stop.is_set():
                k = rng.randrange(6000)
                op = rng.random()
                if op < 0.4:
                    sl.insert(k, k * 7)
                elif op < 0.8:
                    sl.delete(k)
                else:
                    sl.insert(k, k * 7)  # overwrite

        def reader():
            rng = random.Random()
            try:
                while not stop.is_set():
                    lo = rng.randrange(6000)
                    hi = lo + rng.randrange(300)
                    snap = sl.range_scan(lo, hi)
                    # interleave consumption with more writer activity
                    rows = []
                    for pair in snap:
                        rows.append(pair)
                        if rng.random() < 0.1:
                            pass  # yield the GIL to the writers
                    keys = [k for k, _ in rows]
                    if keys != sorted(keys):
                        errors.append(("snapshot not sorted", rows[:5]))
                    if len(set(keys)) != len(keys):
                        errors.append(("snapshot duplicate", rows[:10]))
                    if any(k < lo or k > hi for k in keys):
                        errors.append(("snapshot out of range", lo, hi))
                    if any(v != k * 7 for k, v in rows):
                        errors.append(("snapshot half-updated", rows[:5]))
                    if len(rows) != len(snap):
                        errors.append(("snapshot length mismatch",
                                       len(rows), len(snap)))
            except Exception as exc:  # noqa: BLE001
                errors.append(("reader exception", repr(exc)))

        writers = [threading.Thread(target=writer, args=(s,)) for s in range(2)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in writers + readers:
            t.start()
        deadline = threading.Event()
        deadline.wait(2.0)
        stop.set()
        for t in writers + readers:
            t.join()
        self.assertEqual(errors, [])

    def test_rank_select_under_write_pressure(self):
        """rank/select each take a consistent view under the writer lock.
        Keys 0..2999 form a stable region the writer never touches, so
        rank/select must be exact there; over the dynamic region, any
        returned record must satisfy the value invariant v == k * 7.
        (Positional claims across two separate calls are not asserted:
        each call sees its own consistent snapshot, not a transaction.)"""
        sl = SkipList(rng=random.Random(59))
        for k in range(3000):
            sl.insert(k, k * 7)
        errors = []
        stop = threading.Event()

        def writer():
            k = 3000
            while not stop.is_set():
                sl.insert(k, k * 7)
                if k >= 4000:
                    sl.delete(k - 1000)  # only ever touches keys >= 3000
                k += 1

        def reader():
            rng = random.Random()
            try:
                while not stop.is_set():
                    # stable region: exact, deterministic expectations
                    key = rng.randrange(3000)
                    r = sl.rank(key)
                    if r != key + 1:
                        errors.append(("rank wrong", key, r))
                    got = sl.select(key + 1)
                    if got != (key, key * 7):
                        errors.append(("select wrong", key, got))
                    # dynamic region: results must be value-consistent
                    kth = rng.randrange(1, 6000)
                    got = sl.select(kth)
                    if got is not None and got[1] != got[0] * 7:
                        errors.append(("select half-updated", got))
                    r = sl.rank(rng.randrange(6000))
                    if r is not None and r < 1:
                        errors.append(("rank out of bounds", r))
            except Exception as exc:  # noqa: BLE001
                errors.append(("reader exception", repr(exc)))

        w = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(3)]
        w.start()
        for t in readers:
            t.start()
        threading.Event().wait(2.0)
        stop.set()
        w.join()
        for t in readers:
            t.join()
        self.assertEqual(errors, [])


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
        self.assertEqual(sl.range_scan(100, 199).to_list(),
                         [(k, k * 2) for k in range(100, 200)])
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
                    rows = sl.range_scan(lo, hi).to_list()
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
