"""Concurrency tests: many lock-free readers + one writer.

Invariants checked while a writer churns inserts/deletes:
- get() never raises and never returns a wrong value (value == key * 2).
- scan() output is strictly increasing (no half-linked nodes, no duplicates).
- a key that stays inserted for a whole scan is never skipped by that scan.
"""

import random
import threading
import time
import unittest

from skiplist import SkipList

KEY_SPACE = 4000
RUN_SECONDS = 2.0


class TestConcurrentReadWrite(unittest.TestCase):
    def test_readers_plus_single_writer(self):
        sl = SkipList(rand=random.Random(0).random)
        for k in range(KEY_SPACE // 2):  # pre-populate half the key space
            sl.insert(k, k * 2)

        stop = threading.Event()
        errors: list[str] = []

        def reader_point():
            rng = random.Random(threading.get_ident())
            while not stop.is_set():
                k = rng.randrange(KEY_SPACE)
                try:
                    v = sl.get(k)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"get raised: {exc!r}")
                    return
                if v is not None and v != k * 2:
                    errors.append(f"torn read: get({k}) -> {v}")
                    return

        def reader_scan():
            rng = random.Random(threading.get_ident() + 1)
            while not stop.is_set():
                lo = rng.randrange(KEY_SPACE)
                hi = lo + rng.randrange(200)
                try:
                    out = list(sl.scan(lo, hi))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"scan raised: {exc!r}")
                    return
                keys = [k for k, _ in out]
                if keys != sorted(set(keys)):
                    errors.append(f"scan not strictly increasing: {keys[:20]}...")
                    return
                if any(not (lo <= k <= hi) for k in keys):
                    errors.append("scan out of range")
                    return
                for k, v in out:
                    if v != k * 2:
                        errors.append(f"torn scan entry: ({k}, {v})")
                        return

        def writer():
            rng = random.Random(999)
            k = KEY_SPACE // 2
            while not stop.is_set():
                # rolling window: insert ahead, delete behind
                sl.insert(k % KEY_SPACE, (k % KEY_SPACE) * 2)
                sl.delete((k - KEY_SPACE // 2) % KEY_SPACE)
                k += 1
                if rng.random() < 0.01:
                    time.sleep(0)  # yield occasionally

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader_point) for _ in range(3)]
        threads += [threading.Thread(target=reader_scan) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(RUN_SECONDS)
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # structural sanity after the storm
        keys = [k for k, _ in sl.scan()]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual(len(keys), len(sl))

    def test_scan_never_skips_stable_keys(self):
        # Keys [0, STABLE) stay inserted forever; a concurrent writer churns
        # keys >= STABLE. Every full scan must contain every stable key.
        STABLE = 500
        sl = SkipList(rand=random.Random(1).random)
        for k in range(STABLE * 2):  # stable keys + initial churn window
            sl.insert(k, k)

        stop = threading.Event()
        failures: list[str] = []

        def writer():
            k = STABLE * 2  # churn only keys >= STABLE
            while not stop.is_set():
                sl.insert(k, k)
                sl.delete(k - STABLE)
                k += 1

        def scanner():
            while not stop.is_set():
                got = {k for k, _ in sl.scan(0, STABLE - 1)}
                if got != set(range(STABLE)):
                    failures.append(f"missed {len(set(range(STABLE)) - got)} stable keys")
                    return

        w = threading.Thread(target=writer)
        scanners = [threading.Thread(target=scanner) for _ in range(4)]
        w.start()
        for s in scanners:
            s.start()
        time.sleep(RUN_SECONDS)
        stop.set()
        w.join()
        for s in scanners:
            s.join()
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
