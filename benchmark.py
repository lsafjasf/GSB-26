"""Benchmark: lazy segment tree vs naive point-wise implementation.

Workload: mixed ops (25% range_add, 25% range_assign, 25% range_sum,
25% range_max); half the ranges are full-range [0, n), half are random.
Both implementations run the identical op sequence; query checksums are
compared to guarantee identical results.

Run: python3 benchmark.py
"""

import random
import time

from naive import NaiveArray
from segtree import SegmentTree

SIZES = [100_000, 300_000, 1_000_000]
SEGTREE_OPS = 200_000          # ops for the segment tree at every size
NAIVE_POINT_BUDGET = 2e8       # approx. point-operations budget for naive


def gen_ops(rng, n, q):
    ops = []
    for _ in range(q):
        kind = rng.randrange(4)
        if rng.random() < 0.5:
            l, r = 0, n
        else:
            l = rng.randrange(n)
            r = rng.randrange(l + 1, n + 1)
        if kind == 0:
            ops.append(("add", l, r, rng.randint(-100, 100)))
        elif kind == 1:
            ops.append(("assign", l, r, rng.randint(-100, 100)))
        elif kind == 2:
            ops.append(("sum", l, r, None))
        else:
            ops.append(("max", l, r, None))
    return ops


def run(obj, ops):
    checksum = 0
    for op, l, r, v in ops:
        if op == "add":
            obj.range_add(l, r, v)
        elif op == "assign":
            obj.range_assign(l, r, v)
        elif op == "sum":
            checksum += obj.range_sum(l, r)
        else:
            checksum += obj.range_max(l, r)
    return checksum


def main():
    print(f"{'n':>9} | {'impl':>10} | {'ops':>8} | {'time (s)':>9} | {'ops/s':>12}")
    print("-" * 60)
    for n in SIZES:
        rng = random.Random(1234 + n)
        data = [rng.randint(-1000, 1000) for _ in range(n)]

        q_naive = max(100, int(NAIVE_POINT_BUDGET / n))
        shared = gen_ops(rng, n, q_naive)

        t0 = time.perf_counter()
        cs_nv = run(NaiveArray(data), shared)
        t_nv = time.perf_counter() - t0

        t0 = time.perf_counter()
        cs_st = run(SegmentTree(data), shared)
        t_st_shared = time.perf_counter() - t0
        assert cs_nv == cs_st, f"checksum mismatch at n={n}"

        big = gen_ops(rng, n, SEGTREE_OPS)
        t0 = time.perf_counter()
        run(SegmentTree(data), big)
        t_st_big = time.perf_counter() - t0

        print(f"{n:>9} | {'naive':>10} | {q_naive:>8} | {t_nv:>9.3f} | {q_naive / t_nv:>12.0f}")
        print(f"{n:>9} | {'segtree':>10} | {q_naive:>8} | {t_st_shared:>9.3f} | {q_naive / t_st_shared:>12.0f}")
        print(f"{n:>9} | {'segtree':>10} | {len(big):>8} | {t_st_big:>9.3f} | {len(big) / t_st_big:>12.0f}")
        print("-" * 60)
    print("checksums identical between implementations on every shared workload")


if __name__ == "__main__":
    main()
