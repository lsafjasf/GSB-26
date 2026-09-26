"""Consistency / idempotency / concurrency tests for tls_store."""

import threading
import time
import unittest

from tls_store import CounterStore


class TestSingleThread(unittest.TestCase):
    def test_basic_add_merge_snapshot(self):
        store = CounterStore(start_reaper=False)
        store.add("a", 3)
        store.add("b", 2)
        store.add("a", 1)
        self.assertEqual(store.snapshot(), {})          # nothing merged yet
        store.merge()
        self.assertEqual(store.snapshot(), {"a": 4, "b": 2})
        self.assertEqual(store.total(), 6)
        store.check_invariants()
        store.close()

    def test_merge_idempotent_same_batch(self):
        """Re-merging identical local state must not double count."""
        store = CounterStore(start_reaper=False)
        store.add("k", 7)
        store.merge()
        first = store.snapshot()
        for _ in range(10):                              # duplicate merges
            store.merge()
        self.assertEqual(store.snapshot(), first)
        self.assertEqual(store.total(), 7)
        store.check_invariants()
        store.close()

    def test_merge_idempotent_replay_same_snapshot(self):
        """Replaying the exact same captured snapshot is a no-op."""
        store = CounterStore(start_reaper=False)
        store.add("x", 5)
        store.merge()
        src = store._current_source()
        with src.lock:
            replay = (dict(src.cumulative), dict(src.base))
        before = store.total()
        for _ in range(5):                               # replay stale batch
            with store._lock:
                store._merged[src.source_id] = (dict(replay[0]),
                                                dict(replay[1]))
        self.assertEqual(store.total(), before)
        store.add("x", 2)                                # newer state wins
        store.merge()
        self.assertEqual(store.total(), 7)
        store.check_invariants()
        store.close()

    def test_incremental_merges_accumulate_once(self):
        store = CounterStore(start_reaper=False)
        expected = 0
        for i in range(100):
            store.add("c", 1)
            store.merge()
            expected += 1
            self.assertEqual(store.total(), expected)
        store.check_invariants()
        store.close()

    def test_reset_is_explicit_delete(self):
        store = CounterStore(start_reaper=False)
        store.add("a", 10)
        store.merge()
        self.assertEqual(store.total(), 10)
        store.reset()
        self.assertEqual(store.snapshot(), {})
        store.add("a", 3)                                # counts after reset
        store.merge()
        self.assertEqual(store.total(), 3)
        store.check_invariants()
        store.close()

    def test_negative_add_rejected(self):
        store = CounterStore(start_reaper=False)
        with self.assertRaises(ValueError):
            store.add("a", -1)
        store.close()


class TestMultiThread(unittest.TestCase):
    def _run_workers(self, store, n_threads, n_ops, keys=("k",)):
        barrier = threading.Barrier(n_threads)

        def worker(tid):
            barrier.wait()
            for i in range(n_ops):
                store.add(keys[i % len(keys)], 1)
                if i % 64 == 0:
                    store.merge()
            store.close_local()                              # explicit exit merge

        threads = [threading.Thread(target=worker, args=(t,),
                                    name=f"worker-{t}")
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_interleaved_writes_exact_total(self):
        store = CounterStore(start_reaper=False)
        n_threads, n_ops = 8, 5_000
        self._run_workers(store, n_threads, n_ops)
        store.flush_all()
        self.assertEqual(store.total(), n_threads * n_ops)
        store.check_invariants()
        store.close()

    def test_merge_concurrent_with_writes_monotonic(self):
        """Snapshots taken while writes+merges race must be non-decreasing
        and never exceed the final committed total."""
        store = CounterStore(start_reaper=False)
        n_threads, n_ops = 4, 20_000
        stop = threading.Event()
        observed = []

        def reader():
            last = 0
            while not stop.is_set():
                store.flush_all()                          # merge mid-stream
                value = store.total()
                self.assertGreaterEqual(value, last)       # monotonic
                last = value
                observed.append(value)
            store.flush_all()
            observed.append(store.total())

        r = threading.Thread(target=reader, name="reader")
        r.start()
        self._run_workers(store, n_threads, n_ops)
        stop.set()
        r.join()
        store.flush_all()
        final = store.total()
        self.assertEqual(final, n_threads * n_ops)
        self.assertTrue(all(v <= final for v in observed))
        self.assertEqual(observed[-1], final)
        store.check_invariants()
        store.close()

    def test_dynamic_thread_count(self):
        """Threads come and go in waves; every count must survive."""
        store = CounterStore(start_reaper=False)
        grand_total = 0
        for wave, (n_threads, n_ops) in enumerate(
                [(1, 1_000), (6, 2_000), (2, 3_000), (12, 500), (1, 4_000)]):
            self._run_workers(store, n_threads, n_ops,
                              keys=(f"wave{wave}",))
            grand_total += n_threads * n_ops
            store.flush_all()
            self.assertEqual(store.total(),
                             sum(w[0] * w[1] for w in
                                 [(1, 1000), (6, 2000), (2, 3000),
                                  (12, 500), (1, 4000)][:wave + 1]))
        store.check_invariants()
        store.close()

    def test_thread_exit_without_explicit_close(self):
        """Reaper must merge dead threads' caches (merge-on-exit policy)."""
        store = CounterStore(reap_interval=0.01)
        n_threads, n_ops = 6, 3_000
        expected = n_threads * n_ops

        def worker():
            for _ in range(n_ops):
                store.add("k", 1)
            # no merge(), no close_local(): thread just dies

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        deadline = time.time() + 5.0
        while store.total() != expected and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(store.total(), expected)
        store.check_invariants()
        store.close()

    def test_invariant_total_equals_sum_of_sources(self):
        store = CounterStore(start_reaper=False)
        self._run_workers(store, 5, 2_000, keys=("a", "b", "c"))
        store.flush_all()
        totals = store.source_totals()
        self.assertEqual(sum(totals.values()), store.total())
        self.assertEqual(len([v for v in totals.values() if v > 0]), 5)
        store.check_invariants()
        store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
