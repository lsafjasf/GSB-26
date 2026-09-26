"""Benchmark: time and peak memory for memagg on adversarial distributions.

Usage:
  python3 benchmark.py                          # all scenarios, streaming only
  python3 benchmark.py --scenario high-card --n 2000000 --budget-mb 16
  python3 benchmark.py --check                  # also run naive in-memory impl
                                                # and verify identical results
Peak memory is the process RSS high-water mark (resource.ru_maxrss).
"""

import argparse
import random
import resource
import sys
import time

sys.path.insert(0, ".")
from memagg import AGGS, GroupBy, Pivot
from test_memagg import naive_groupby

ALL_AGGS = list(AGGS)


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gen_high_card(n, key_space, seed=1):
    rng = random.Random(seed)
    for _ in range(n):
        r = rng.random()
        v = None if r < 0.02 else rng.randint(0, 10_000)
        yield rng.randrange(key_space), v


def gen_skew(n, hot_frac=0.99, cold_space=1000, seed=2):
    rng = random.Random(seed)
    for _ in range(n):
        r = rng.random()
        k = "hot" if r < hot_frac else f"cold{rng.randrange(cold_space)}"
        v = None if rng.random() < 0.02 else rng.randint(0, 10_000)
        yield k, v


def gen_pivot(n, n_rows, n_cols, seed=3):
    rng = random.Random(seed)
    for _ in range(n):
        yield (f"r{rng.randrange(n_rows)}", rng.randrange(n_cols),
               rng.randint(0, 10_000))


def run_streaming(records, budget_bytes, aggs=ALL_AGGS):
    """Consume results as a stream (the memory-bounded usage pattern)."""
    gb = GroupBy(aggs, memory_budget=budget_bytes)
    t0 = time.perf_counter()
    gb.update(records)
    t_add = time.perf_counter() - t0
    t0 = time.perf_counter()
    n_groups, checksum = 0, 0
    for _key, aggs in gb.results():
        n_groups += 1
        checksum += aggs.get("count", 0)
    t_merge = time.perf_counter() - t0
    return (n_groups, checksum), t_add, t_merge, gb.spill_count


def run_naive(records, aggs=ALL_AGGS):
    t0 = time.perf_counter()
    res = naive_groupby(records, aggs)
    return res, time.perf_counter() - t0


def scenario_groupby(name, gen, n, budget_bytes, check):
    print(f"\n=== {name}: n={n:,}, budget={budget_bytes >> 20} MiB ===")
    records = list(gen) if check else gen  # naive needs a re-iterable
    base = peak_rss_mb()
    (n_groups, checksum), t_add, t_merge, spills = run_streaming(
        records, budget_bytes)
    peak = peak_rss_mb()
    print(f"memagg  : groups={n_groups:,}  spills={spills}  "
          f"ingest={t_add:.2f}s  merge={t_merge:.2f}s  "
          f"total={t_add + t_merge:.2f}s  peakRSS={peak:.0f} MiB "
          f"(+{peak - base:.0f} MiB)")
    if check:
        base = peak_rss_mb()
        expected, t_naive = run_naive(records)
        got = GroupBy(ALL_AGGS, memory_budget=budget_bytes)
        got.update(records)
        got = got.to_dict()
        peak = peak_rss_mb()
        assert got == expected, "MISMATCH between memagg and naive!"
        assert sum(v["count"] for v in expected.values()) == checksum
        print(f"naive   : groups={len(expected):,}  total={t_naive:.2f}s  "
              f"peakRSS={peak:.0f} MiB (+{peak - base:.0f} MiB)")
        print("check   : results identical ✓")


def scenario_pivot(n, n_rows, n_cols, budget_bytes):
    print(f"\n=== pivot: n={n:,}, grid={n_rows}x{n_cols}, "
          f"budget={budget_bytes >> 20} MiB ===")
    pv = Pivot("sum", memory_budget=budget_bytes)
    base = peak_rss_mb()
    t0 = time.perf_counter()
    pv.update(gen_pivot(n, n_rows, n_cols))
    t_add = time.perf_counter() - t0
    t0 = time.perf_counter()
    res = pv.result()
    t_merge = time.perf_counter() - t0
    peak = peak_rss_mb()
    print(f"memagg  : cells={len(res['cells']):,}  spills={pv.spill_count}  "
          f"ingest={t_add:.2f}s  merge={t_merge:.2f}s  "
          f"total={t_add + t_merge:.2f}s  peakRSS={peak:.0f} MiB "
          f"(+{peak - base:.0f} MiB)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario",
                   choices=["high-card", "skew", "pivot", "all"], default="all")
    p.add_argument("--n", type=int, default=2_000_000)
    p.add_argument("--key-space", type=int, default=2_500_000)
    p.add_argument("--budget-mb", type=int, default=16)
    p.add_argument("--check", action="store_true",
                   help="also run the naive in-memory reference and compare")
    args = p.parse_args()
    budget = args.budget_mb << 20

    if args.scenario in ("high-card", "all"):
        scenario_groupby(
            "high-cardinality (~1.4M distinct groups)",
            gen_high_card(args.n, args.key_space), args.n, budget, args.check)
    if args.scenario in ("skew", "all"):
        scenario_groupby(
            "extreme skew (99% one group)",
            gen_skew(args.n), args.n, budget, args.check)
    if args.scenario in ("pivot", "all"):
        scenario_pivot(min(args.n, 1_000_000), 5_000, 200, budget)


if __name__ == "__main__":
    main()
