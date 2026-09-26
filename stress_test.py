"""Stress test (对拍): SegmentTree vs NaiveArray.

Covers: single-point ranges, full range, adjacent ranges, interleaved
add/assign, out-of-bounds rejection, empty op sequences, n = 1.
Every query result is compared after each operation; any mismatch aborts.

Run: python3 stress_test.py [trials] [seed]
"""

import random
import sys

from naive import NaiveArray
from segtree import SegmentTree

OPS = ("add", "assign", "sum", "max")


def make_range(rng, n):
    """Random range with extra weight on edge shapes."""
    kind = rng.random()
    if kind < 0.15:  # single point
        l = rng.randrange(n)
        return l, l + 1
    if kind < 0.30:  # full range
        return 0, n
    if kind < 0.45:  # adjacent pair covering [0, n)
        if n == 1:
            return 0, 1
        k = rng.randrange(1, n)
        return (0, k) if rng.random() < 0.5 else (k, n)
    l = rng.randrange(n)
    r = rng.randrange(l + 1, n + 1)
    return l, r


def run_trial(rng, n, num_ops):
    data = [rng.randint(-100, 100) for _ in range(n)]
    st = SegmentTree(data)
    nv = NaiveArray(data)
    for step in range(num_ops):
        op = rng.choice(OPS)
        l, r = make_range(rng, n)
        if op == "add":
            v = rng.randint(-50, 50)
            st.range_add(l, r, v)
            nv.range_add(l, r, v)
        elif op == "assign":
            v = rng.randint(-50, 50)
            st.range_assign(l, r, v)
            nv.range_assign(l, r, v)
        elif op == "sum":
            got, want = st.range_sum(l, r), nv.range_sum(l, r)
            assert got == want, f"sum mismatch step={step} [{l},{r}): {got} != {want}"
        else:
            got, want = st.range_max(l, r), nv.range_max(l, r)
            assert got == want, f"max mismatch step={step} [{l},{r}): {got} != {want}"
    assert st.to_list() == nv.to_list(), f"final array mismatch (n={n})"


def directed_tests():
    # n = 1
    st, nv = SegmentTree([5]), NaiveArray([5])
    st.range_add(0, 1, 3); nv.range_add(0, 1, 3)
    st.range_assign(0, 1, -2); nv.range_assign(0, 1, -2)
    assert st.range_sum(0, 1) == nv.range_sum(0, 1) == -2
    assert st.range_max(0, 1) == nv.range_max(0, 1) == -2

    # assign overrides earlier adds; adds after assign accumulate
    st, nv = SegmentTree([0] * 10), NaiveArray([0] * 10)
    for obj in (st, nv):
        obj.range_add(0, 10, 5)
        obj.range_assign(2, 8, 100)   # wipes the +5 on [2,8)
        obj.range_add(2, 8, 1)        # accumulates on top of 100
        obj.range_assign(4, 6, -7)    # nested re-assign
        obj.range_add(0, 10, 2)
    assert st.to_list() == nv.to_list() == [7, 7, 103, 103, -5, -5, 103, 103, 7, 7]
    assert st.range_sum(0, 10) == nv.range_sum(0, 10)
    assert st.range_max(0, 10) == nv.range_max(0, 10)

    # adjacent ranges are independent
    st, nv = SegmentTree([1] * 6), NaiveArray([1] * 6)
    st.range_assign(0, 3, 9); nv.range_assign(0, 3, 9)
    st.range_add(3, 6, 4); nv.range_add(3, 6, 4)
    assert st.to_list() == nv.to_list() == [9, 9, 9, 5, 5, 5]

    # empty-range behavior
    st = SegmentTree([1, 2, 3])
    st.range_add(1, 1, 99)          # no-op
    st.range_assign(2, 2, 99)       # no-op
    assert st.range_sum(1, 1) == 0
    assert st.to_list() == [1, 2, 3]
    for bad in [lambda: st.range_max(1, 1)]:
        try:
            bad(); raise SystemExit("expected ValueError for empty max")
        except ValueError:
            pass

    # out-of-bounds rejected by both implementations
    st, nv = SegmentTree([1, 2, 3]), NaiveArray([1, 2, 3])
    bad_ranges = [(-1, 2), (0, 4), (2, 1), (0, 100), (-5, -1)]
    for l, r in bad_ranges:
        for fn in ("range_add", "range_assign", "range_sum", "range_max"):
            args = (l, r, 1) if fn in ("range_add", "range_assign") else (l, r)
            for obj in (st, nv):
                try:
                    getattr(obj, fn)(*args)
                    raise SystemExit(f"expected IndexError: {fn}{args}")
                except IndexError:
                    pass

    # empty op sequence: build and query only
    st, nv = SegmentTree([3, 1, 4]), NaiveArray([3, 1, 4])
    assert st.range_sum(0, 3) == nv.range_sum(0, 3) == 8
    assert st.range_max(0, 3) == nv.range_max(0, 3) == 4

    # empty data rejected
    for cls in (SegmentTree, NaiveArray):
        try:
            cls([]); raise SystemExit("expected ValueError for empty data")
        except ValueError:
            pass


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260926
    rng = random.Random(seed)

    directed_tests()
    print("directed edge-case tests: OK")

    for t in range(trials):
        n = rng.choice([1, 1, 2, 3, 5, 8, 17, 33, 64, 100])
        num_ops = rng.choice([0, 1, 10, 50, 200, 500])  # 0 => empty op sequence
        run_trial(rng, n, num_ops)
        if (t + 1) % 50 == 0:
            print(f"  {t + 1}/{trials} trials passed")
    print(f"stress test: ALL {trials} trials PASSED (seed={seed})")


if __name__ == "__main__":
    main()
