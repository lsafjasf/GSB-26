"""Self-tests & benchmarks for bloom_filter.py.  Run: python3 selftest.py"""

import sys
import time

from bloom_filter import BloomFilter, ScalableBloomFilter

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{PASS if ok else FAIL}] {name}" + (f"  | {detail}" if detail else ""))


def measure_fpr(bf, probes, probe_prefix="probe"):
    fp = 0
    for i in range(probes):
        if f"{probe_prefix}-{i}" in bf:
            fp += 1
    return fp / probes


def test_empty():
    bf = BloomFilter(capacity=1000, error_rate=0.01, seed=1)
    ok = len(bf) == 0 and "anything" not in bf and measure_fpr(bf, 5000) == 0.0
    check("empty set: no membership, FPR = 0", ok)


def test_single():
    bf = BloomFilter(capacity=1000, error_rate=0.01, seed=2)
    bf.add("only-one")
    ok = "only-one" in bf and len(bf) == 1
    fpr = measure_fpr(bf, 20000)
    # single element sets k bits; FPR ~ (k/m)^k, far below target
    ok = ok and fpr < 0.01
    check("single element: found, FPR near 0", ok, f"measured FPR={fpr:.5f}")


def test_basic_fpr():
    print("\n-- basic Bloom filter: target vs theoretical vs measured FPR --")
    print(f"{'target':>8} {'n':>7} {'bits':>9} {'k':>3} {'bytes':>8} "
          f"{'theory':>9} {'measured':>9} {'ratio':>6}")
    for p, n, probes in ((0.001, 50_000, 300_000), (0.10, 20_000, 300_000)):
        bf = BloomFilter(capacity=n, error_rate=p, seed=42)
        for i in range(n):
            bf.add(f"item-{i}")
        theory = bf.expected_fpr(n)
        measured = measure_fpr(bf, probes)
        ratio = measured / theory
        print(f"{p:>8.3%} {n:>7} {bf.num_bits:>9} {bf.num_hashes:>3} "
              f"{bf.num_bytes:>8} {theory:>9.4%} {measured:>9.4%} {ratio:>6.2f}")
        # measured should be close to theory (within 2x / 0.4x statistical band)
        check(f"basic FPR p={p}: measured ~= theory", 0.4 < ratio < 2.0,
              f"theory={theory:.4%} measured={measured:.4%}")
        # all inserted items must be found (no false negatives)
        ok = all(f"item-{i}" in bf for i in range(0, n, max(1, n // 500)))
        check(f"basic FPR p={p}: no false negatives", ok)


def test_overflow():
    print("\n-- overflow: insert 5x capacity into basic vs scalable --")
    n, p = 10_000, 0.01
    bf = BloomFilter(capacity=n, error_rate=p, seed=7)
    sbf = ScalableBloomFilter(initial_capacity=n, error_rate=p,
                              growth_factor=2, tightening_ratio=0.9, seed=7)
    total = 5 * n
    for i in range(total):
        bf.add(f"item-{i}")
        sbf.add(f"item-{i}")
    fpr_basic = measure_fpr(bf, 200_000)
    fpr_scalable = measure_fpr(sbf, 200_000)
    bound = sbf.error_rate_bound()
    print(f"basic    : inserted {total} into capacity {n} -> FPR={fpr_basic:.3%} "
          f"(target was {p:.1%}, degraded)")
    print(f"scalable : layers={len(sbf.filters)} FPR={fpr_scalable:.3%} "
          f"bound(sum p_i)={bound:.3%} asymptotic p0/(1-r)={sbf.asymptotic_bound():.3%}")
    check("overflow: basic FPR degrades far beyond target", fpr_basic > 5 * p,
          f"{fpr_basic:.3%} vs target {p:.1%}")
    check("overflow: scalable FPR stays under union bound", fpr_scalable <= bound,
          f"{fpr_scalable:.3%} <= {bound:.3%}")
    ok = all(f"item-{i}" in sbf for i in range(0, total, 250))
    check("overflow: scalable has no false negatives", ok)


def test_scalable_fpr_vs_bound():
    print("\n-- scalable Bloom filter: measured FPR vs bound --")
    print(f"{'p0':>7} {'r':>5} {'s':>3} {'layers':>6} {'inserted':>8} "
          f"{'bound':>8} {'p0/(1-r)':>9} {'measured':>9}")
    for p0, r, s in ((0.001, 0.9, 2), (0.10, 0.5, 2)):
        sbf = ScalableBloomFilter(initial_capacity=2_000, error_rate=p0,
                                  growth_factor=s, tightening_ratio=r, seed=99)
        total = 60_000
        for i in range(total):
            sbf.add(f"elem-{i}")
        measured = measure_fpr(sbf, 300_000)
        bound = sbf.error_rate_bound()
        asym = sbf.asymptotic_bound()
        print(f"{p0:>7.3%} {r:>5} {s:>3} {len(sbf.filters):>6} {total:>8} "
              f"{bound:>8.3%} {asym:>9.3%} {measured:>9.4%}")
        check(f"scalable p0={p0},r={r}: measured <= bound <= p0/(1-r)",
              measured <= bound <= asym,
              f"measured={measured:.4%} bound={bound:.3%} asym={asym:.3%}")


def test_seed_reproducibility():
    a = BloomFilter(1000, 0.01, seed=123)
    b = BloomFilter(1000, 0.01, seed=123)
    c = BloomFilter(1000, 0.01, seed=456)
    for i in range(500):
        a.add(f"x-{i}")
        b.add(f"x-{i}")
        c.add(f"x-{i}")
    check("seed: same seed -> identical bit arrays", a._bits == b._bits)
    check("seed: different seed -> different bit arrays", a._bits != c._bits)


def bench_memory_and_throughput():
    print("\n-- memory & throughput (n = 200,000, p = 1%) --")
    n, p = 200_000, 0.01
    items = [f"key-{i:08d}" for i in range(n)]

    bf = BloomFilter(capacity=n, error_rate=p, seed=5)
    t0 = time.perf_counter()
    for it in items:
        bf.add(it)
    t_add = time.perf_counter() - t0

    t0 = time.perf_counter()
    hits = sum(1 for it in items if it in bf)
    t_query = time.perf_counter() - t0

    exact = set(items)
    exact_bytes = sys.getsizeof(exact) + sum(sys.getsizeof(s) for s in items)
    bf_bytes = bf.num_bytes

    print(f"insert throughput : {n / t_add:>12,.0f} ops/s")
    print(f"query throughput  : {n / t_query:>12,.0f} ops/s (hits={hits})")
    print(f"bloom memory      : {bf_bytes:>12,} bytes "
          f"({bf_bytes / n:.1f} B/elem, {bf.num_bits / n:.2f} bits/elem, k={bf.num_hashes})")
    print(f"exact set memory  : {exact_bytes:>12,} bytes ({exact_bytes / n:.1f} B/elem)")
    print(f"memory ratio      : exact / bloom = {exact_bytes / bf_bytes:.1f}x")
    check("bench: all inserted items found", hits == n)
    check("bench: bloom uses much less memory than exact set",
          bf_bytes < exact_bytes / 4, f"{exact_bytes / bf_bytes:.1f}x smaller")


if __name__ == "__main__":
    test_empty()
    test_single()
    test_basic_fpr()
    test_overflow()
    test_scalable_fpr_vs_bound()
    test_seed_reproducibility()
    bench_memory_and_throughput()
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")
        sys.exit(1)
