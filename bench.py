"""Benchmarks: time & peak-RSS for streaming (budgeted, spilling) aggregation
vs. the naive fully in-memory reference, on high-cardinality and skewed
distributions.  Also verifies both produce identical results.

Run:  python3 bench.py
"""

import gc
import os
import random
import resource
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlimit_agg import CrossTab, GroupByAggregator, reference_groupby

N = 1_000_000
BUDGET = 16 << 20  # 16 MiB
AGGS = {"count": None, "sum": lambda r: r[1], "min": lambda r: r[1],
        "max": lambda r: r[1], "count_distinct": lambda r: r[1]}


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gen_high_card(n):
    # every record its own group -> exactly n distinct groups
    return [(i, random.randrange(1000)) for i in range(n)]


def gen_skewed(n):
    # 99.9% of records in one hot group, rest spread over 100k groups
    out = []
    for i in range(n):
        if random.random() < 0.999:
            out.append(("hot", random.randrange(1000)))
        else:
            out.append((i, random.randrange(1000)))
    return out


def run_streaming(data):
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    agg = GroupByAggregator(key=lambda r: r[0], aggs=AGGS,
                            memory_budget=BUDGET)
    agg.add_many(data)
    ngroups, check = 0, 0
    for key, stats in agg.iter_results():  # streamed, never materialized
        ngroups += 1
        check += stats["count"] + stats["count_distinct"]
    t1 = time.perf_counter()
    peak = tracemalloc.get_traced_memory()[1] / (1 << 20)
    tracemalloc.stop()
    return {"time": t1 - t0, "mem": peak,
            "groups": ngroups, "spills": agg.spill_count,
            "spill_mb": agg.spill_bytes / (1 << 20), "check": check}


def run_reference(data):
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    want = reference_groupby(data, lambda r: r[0], AGGS)
    t1 = time.perf_counter()
    peak = tracemalloc.get_traced_memory()[1] / (1 << 20)
    tracemalloc.stop()
    return {"time": t1 - t0, "mem": peak, "groups": len(want)}, want


def verify(data, want):
    agg = GroupByAggregator(key=lambda r: r[0], aggs=AGGS,
                            memory_budget=BUDGET)
    agg.add_many(data)
    got = agg.result()
    assert got == want, "MISMATCH: streaming != reference"
    del got


def bench_crosstab(data2):
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    ct = CrossTab((lambda r: r[0]), (lambda r: r[1]),
                  op="sum", value=lambda r: r[2], memory_budget=BUDGET)
    ct.add_many(data2)
    res = ct.result()
    t1 = time.perf_counter()
    peak = tracemalloc.get_traced_memory()[1] / (1 << 20)
    tracemalloc.stop()
    return {"time": t1 - t0, "mem": peak,
            "cells": len(res.cells), "spills": ct.spill_count}


def main():
    random.seed(20260926)
    print("# memlimit_agg benchmark  (budget = %d MiB, N = %d records)\n"
          % (BUDGET >> 20, N))
    hdr = ("%-14s %-10s %8s %9s %8s %8s | %8s %9s | %s"
           % ("distribution", "impl", "groups", "time(s)", "peakMB",
              "spills", "spillMB", "check", "verify"))
    print(hdr)
    print("-" * len(hdr))
    for name, gen in [("high-card(1M)", gen_high_card),
                      ("skewed(99.9%)", gen_skewed)]:
        data = gen(N)
        s = run_streaming(data)
        r, want = run_reference(data)
        verify(data, want)
        print("%-14s %-10s %8d %9.2f %8.1f %8d %8.1f | %8d %9s | OK"
              % (name, "streaming", s["groups"], s["time"], s["mem"],
                 s["spills"], s["spill_mb"], s["check"], ""))
        print("%-14s %-10s %8d %9.2f %8.1f %8s %8s | %8s %9s | (ref)"
              % (name, "in-memory", r["groups"], r["time"], r["mem"],
                 "-", "-", "-", ""))
        del data, want
        gc.collect()

    # 2-D cross-tab on skewed data: hot row x 1000 cols
    data2 = [("hot" if random.random() < 0.99 else "r%d" % (i % 50),
              i % 1000, random.randrange(100)) for i in range(N)]
    c = bench_crosstab(data2)
    print("%-14s %-10s %8d %9.2f %8.1f %8d %8s | %8s %9s | cells"
          % ("crosstab-2D", "streaming", c["cells"], c["time"], c["mem"],
             c["spills"], "-", "-", ""))


if __name__ == "__main__":
    main()
