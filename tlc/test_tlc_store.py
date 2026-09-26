"""Consistency + idempotency assertion tests for tlc_store.

Run: python3 test_tlc_store.py
Stdlib only. Exits non-zero on any failed assertion.
"""

import random
import sys
import threading
import time

from tlc_store import ThreadLocalMergeStore


def test_single_thread():
    s = ThreadLocalMergeStore()
    assert s.total() == 0
    for _ in range(1000):
        s.add("hits")
    assert s.total() == 0, "local writes must not leak into the global view before merge"
    assert s.pending() == 1000
    n = s.flush()
    assert n == 1000
    assert s.snapshot() == {"hits": 1000}
    assert s.flush() == 0, "second flush after empty local cache must add nothing"
    s.assert_invariants()


def test_merge_idempotent_low_level():
    """The core idempotency assertion: re-merging the SAME batch
    (same source_id, same seq, same deltas) counts exactly once."""
    s = ThreadLocalMergeStore()
    batch = {"a": 3, "b": 5}
    assert s.merge_batch(source_id=99, seq=1, deltas=dict(batch)) is True
    assert s.total() == 8
    # repeat the exact same batch N times: must be ignored every time
    for _ in range(100):
        assert s.merge_batch(99, 1, dict(batch)) is False
    assert s.snapshot() == {"a": 3, "b": 5}
    assert s.total() == 8
    # a later (higher seq) batch from the same source applies normally
    assert s.merge_batch(99, 2, {"a": 1}) is True
    assert s.snapshot() == {"a": 4, "b": 5}
    # replaying an OLD seq after newer batches also counts nothing
    assert s.merge_batch(99, 1, dict(batch)) is False
    assert s.merge_batch(99, 2, {"a": 1}) is False
    assert s.total() == 9
    s.assert_invariants()


def test_flush_idempotent_under_duplicate_call():
    """Local flush followed by a forged duplicate flush must not
    double-count (high-water mark per source protects this)."""
    s = ThreadLocalMergeStore()
    s.add("k", 10)
    s.flush()
    assert s.total() == 10
    # find this source's high-water mark and replay it
    sid = next(iter(s._applied_seq))
    assert s.merge_batch(sid, s._applied_seq[sid], {"k": 10}) is False
    assert s.total() == 10
    s.assert_invariants()


def test_multithread_interleaved():
    s = ThreadLocalMergeStore()
    n_threads, per_thread, keys = 8, 5000, ("x", "y", "z")

    def worker():
        for i in range(per_thread):
            s.add(keys[i % len(keys)])
            if i % 137 == 0:  # interleave flushes
                s.flush()
        s.close()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # all local writes merged on close: nothing lost, nothing double counted
    assert s.total() == n_threads * per_thread
    view = s.snapshot()
    assert sum(view.values()) == n_threads * per_thread
    assert all(v >= 0 for v in view.values())
    s.assert_invariants()


def test_merge_concurrent_with_writes():
    """Writers keep adding while a reader merges/reads. The global view
    must always be internally consistent and the final total exact."""
    s = ThreadLocalMergeStore()
    n_threads, per_thread = 6, 8000
    stop = threading.Event()
    errors = []

    def reader():
        try:
            while not stop.is_set():
                # consistent point-in-time snapshot: view and total
                # must come from one atomic read
                view, tot = s.snapshot_with_total()
                assert sum(view.values()) == tot
                s.assert_invariants()
        except AssertionError as e:
            errors.append(e)

    def flusher():
        try:
            while not stop.is_set():
                s.flush_all()
                s.assert_invariants()
                time.sleep(0.0002)
        except AssertionError as e:
            errors.append(e)

    def writer(seed):
        rng = random.Random(seed)
        for _ in range(per_thread):
            s.add(rng.choice(("a", "b", "c", "d")))
            if rng.random() < 0.05:
                s.flush()

    readers = [threading.Thread(target=reader) for _ in range(2)]
    flushers = [threading.Thread(target=flusher) for _ in range(2)]
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in readers + flushers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers + flushers:
        t.join()
    s.flush_all()
    assert not errors, f"consistency violations observed: {errors}"
    assert s.total() == n_threads * per_thread
    assert s.total() == sum(s.snapshot().values())
    s.assert_invariants()


def test_dynamic_threads_and_exit_reap():
    """Threads come and go dynamically; caches of dead threads must be
    merged automatically by the reaper — no lost counts, no dupes."""
    s = ThreadLocalMergeStore()
    expected = 0

    def batch_worker(n, flush_at_exit):
        for _ in range(n):
            s.add("ev")
        if flush_at_exit:
            s.close()
        # otherwise leave the local cache behind; reaper must flush it

    for round_no in range(5):
        sizes = [random.randint(100, 1000) for _ in range(random.randint(1, 6))]
        expected += sum(sizes)
        half_close = [threading.Thread(target=batch_worker, args=(n, i % 2 == 0))
                      for i, n in enumerate(sizes)]
        for t in half_close:
            t.start()
        for t in half_close:
            t.join()
        # dead threads without explicit close(): reaper runs inside total()
        assert s.total() == expected, (
            f"round {round_no}: reaper lost/duplicated counts "
            f"({s.total()} != {expected})"
        )
        s.assert_invariants()

    # new threads after old ones died must not be affected by reaping
    def late_worker():
        for _ in range(250):
            s.add("ev")
    t = threading.Thread(target=late_worker)
    t.start(); t.join()
    # total() reaps dead threads -> the leftover 250 are merged, not lost
    assert s.total() == expected + 250
    s.assert_invariants()


def test_discard_policy():
    s = ThreadLocalMergeStore()
    s.add("q", 7)
    s.discard_local()
    assert s.pending() == 0
    s.flush()
    assert s.total() == 0
    s.add("q", 3)
    s.flush()
    assert s.total() == 3
    s.assert_invariants()


def test_global_view_monotone_under_churn():
    s = ThreadLocalMergeStore()
    prev = 0

    def worker():
        for _ in range(3000):
            s.add("m")
            if random.random() < 0.1:
                s.flush()
        s.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        v = s.total()
        assert v >= prev, "global total must be monotonically non-decreasing"
        prev = v
    for t in threads:
        t.join()
    assert s.total() == 10 * 3000
    s.assert_invariants()


def main():
    tests = [
        test_single_thread,
        test_merge_idempotent_low_level,
        test_flush_idempotent_under_duplicate_call,
        test_multithread_interleaved,
        test_merge_concurrent_with_writes,
        test_dynamic_threads_and_exit_reap,
        test_discard_policy,
        test_global_view_monotone_under_churn,
    ]
    random.seed(42)
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    sys.exit(main())
