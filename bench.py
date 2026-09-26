"""Benchmark: 1M updates across 100+ versions; query latency and memory.

Run: python3 bench.py
"""

import gc
import random
import resource
import statistics
import sys
import time
import tracemalloc

from versioned_store import VersionedStore, _Node


def rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def pct(samples, q):
    s = sorted(samples)
    return s[min(len(s) - 1, int(len(s) * q))]


def phase_space_growth():
    """Space growth vs. version count: 1 new/changed key per version."""
    print("== Phase 1: space growth vs. version count (1 update per version) ==")
    tracemalloc.start()
    store = VersionedStore()
    marks = {10, 100, 1_000, 10_000, 50_000, 100_000, 200_000}
    print(f"{'versions':>10} {'keys':>10} {'tracked MiB':>12} {'MiB delta':>10} {'bytes/version':>14}")
    prev_mem, prev_v = 0, 0
    for v in range(1, 200_001):
        store.put(v, v)
        if v in marks:
            mem = tracemalloc.get_traced_memory()[0]
            delta = mem - prev_mem
            print(f"{v:>10} {v:>10} {mem/2**20:>12.2f} {delta/2**20:>10.2f} "
                  f"{delta/max(1, v-prev_v):>14.0f}")
            prev_mem, prev_v = mem, v
    tracemalloc.stop()
    print(f"(node object size: {sys.getsizeof(_Node(0, 0, None, None))} bytes, "
          f"tree depth ~ {store._roots[-1].height})")
    print()


def phase_million_updates():
    """1M updates committed as 1000 versions (1000 updates per commit)."""
    print("== Phase 2: 1,000,000 updates / 1000 versions ==")
    rng = random.Random(7)
    key_space = 500_000
    store = VersionedStore()
    gc.disable()
    tracemalloc.start()
    t0 = time.perf_counter()
    for _ in range(1000):
        batch = [(rng.randrange(key_space), rng.randrange(1 << 30))
                 for _ in range(1000)]
        store.commit(batch)
    commit_s = time.perf_counter() - t0
    tracked = tracemalloc.get_traced_memory()[0]
    gc.enable()
    latest = store.latest_version
    print(f"commit: {1_000_000/commit_s:,.0f} updates/s "
          f"({commit_s:.2f}s total, {commit_s*1e6/1000:,.0f} us/1k-batch)")
    print(f"versions: {latest}, keys: {store.count():,}, "
          f"tracked memory: {tracked/2**20:.1f} MiB, "
          f"~{tracked/latest:,.0f} bytes/version amortized")

    # Point queries at random historical versions.
    gets = []
    samples = [(rng.randrange(key_space), rng.randint(1, latest)) for _ in range(20_000)]
    t0 = time.perf_counter()
    for k, v in samples:
        a = time.perf_counter_ns()
        store.get(k, v)
        gets.append(time.perf_counter_ns() - a)
    get_s = time.perf_counter() - t0

    # Range scans of 100 consecutive keys at random historical versions.
    scans = []
    t0 = time.perf_counter()
    for _ in range(2_000):
        lo = rng.randrange(key_space - 100)
        v = rng.randint(1, latest)
        a = time.perf_counter_ns()
        store.scan(lo, lo + 100, version=v)
        scans.append(time.perf_counter_ns() - a)
    scan_s = time.perf_counter() - t0

    print(f"point get (historical):  avg {statistics.mean(gets)/1000:6.1f} us  "
          f"p50 {pct(gets,0.5)/1000:6.1f} us  p99 {pct(gets,0.99)/1000:6.1f} us  "
          f"({len(gets)/get_s:,.0f} qps)")
    print(f"range scan 100 keys:     avg {statistics.mean(scans)/1000:6.1f} us  "
          f"p50 {pct(scans,0.5)/1000:6.1f} us  p99 {pct(scans,0.99)/1000:6.1f} us  "
          f"({len(scans)/scan_s:,.0f} qps)")
    print(f"process max RSS: {rss_mib():.0f} MiB")

    # GC: reclaim all but the newest 100 versions, verify retained correctness.
    print("== Phase 3: gc to newest 100 versions ==")
    checks = [(k, rng.randint(latest - 99, latest)) for k, _ in
              rng.sample(samples, 2000)]
    expected = [(k, v, store.get(k, v)) for k, v in checks]
    before = tracemalloc.get_traced_memory()[0]
    dropped = store.gc(min_version=latest - 99)
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    print(f"dropped {len(dropped)} versions, tracked memory "
          f"{before/2**20:.1f} -> {after/2**20:.1f} MiB")
    bad = sum(1 for k, v, want in expected if store.get(k, v) != want)
    print(f"post-gc correctness: {len(expected)-bad}/{len(expected)} sampled "
          f"(key, version) reads match pre-gc values")
    assert bad == 0


if __name__ == "__main__":
    phase_space_growth()
    phase_million_updates()
