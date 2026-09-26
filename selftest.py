"""Self-test & benchmark for bloom.py.  Run:  python3 selftest.py"""

import random
import sys
import time

from bloom import (BloomFilter, CountingBloomFilter, ScalableBloomFilter,
                   optimal_num_bits, optimal_num_hashes)

SEED = 42


def make_keys(prefix, n):
    return [f"{prefix}-{i}" for i in range(n)]


def measure_fp(bf, negatives):
    return sum(1 for x in negatives if x in bf) / len(negatives)


def check_no_false_negatives(bf, items):
    missing = sum(1 for x in items if x not in bf)
    assert missing == 0, f"false negatives detected: {missing}"


def report(title, rows):
    print(f"\n== {title} ==")
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"  {k:<{width}} : {v}")


def test_empty_and_single():
    bf = BloomFilter(capacity=1000, error_rate=0.01, seed=SEED)
    negatives = make_keys("neg", 100_000)
    fp_empty = measure_fp(bf, negatives)
    assert fp_empty == 0.0
    bf.add("only-one")
    assert "only-one" in bf
    fp_single = measure_fp(bf, negatives)
    report("空集 / 单元素", [
        ("空集误判率 (10万阴性样本)", f"{fp_empty:.6%} (理论 0)"),
        ("单元素误判率", f"{fp_single:.6%} (理论 {bf.theoretical_fp(1):.6%})"),
        ("单元素可查询", "only-one in bf -> True"),
    ])


def test_target_rates():
    rows = []
    for p in (0.1, 0.001):
        n = 100_000
        bf = BloomFilter(capacity=n, error_rate=p, seed=SEED)
        for x in make_keys("pos", n):
            bf.add(x)
        check_no_false_negatives(bf, make_keys("pos", n))
        measured = measure_fp(bf, make_keys("neg", 200_000))
        rows.append((f"目标 p={p:.1%} 实测误判率",
                     f"{measured:.4%}  (理论 {bf.theoretical_fp():.4%}, "
                     f"m={bf.num_bits} bits, k={bf.num_hashes})"))
    report("目标误判率 0.1% 与 10%（n=10万，阴性样本20万）", rows)


def test_overfill():
    rows = []
    n0, p = 10_000, 0.01
    for multiple in (1, 2, 5):
        bf = BloomFilter(capacity=n0, error_rate=p, seed=SEED)
        items = make_keys("pos", n0 * multiple)
        for x in items:
            bf.add(x)
        check_no_false_negatives(bf, items)
        measured = measure_fp(bf, make_keys("neg", 100_000))
        rows.append((f"超量 {multiple}x (插入 {n0*multiple})",
                     f"实测 {measured:.4%} / 理论 {bf.theoretical_fp():.4%}"))
    report("远超预估量（预估 1万, p=1%）", rows)


def test_scalable():
    rows = []
    total = 100_000
    for growth in (2.0, 4.0):
        sbf = ScalableBloomFilter(initial_capacity=10_000, error_rate=0.01,
                                  growth=growth, tightening=0.9, seed=SEED)
        items = make_keys("pos", total)
        for x in items:
            sbf.add(x)
        check_no_false_negatives(sbf, items)
        measured = measure_fp(sbf, make_keys("neg", 200_000))
        rows.append((f"growth={growth}",
                     f"层数={sbf.num_layers}, 实测 {measured:.4%} / "
                     f"上界 {sbf.theoretical_fp_bound():.4%} / 目标 1.0000%, "
                     f"内存 {sbf.nbytes/1024:.1f} KiB"))
    # 可复现性：同种子两次构建位图完全一致
    a = ScalableBloomFilter(1000, 0.01, seed=7)
    b = ScalableBloomFilter(1000, 0.01, seed=7)
    c = ScalableBloomFilter(1000, 0.01, seed=8)
    for x in make_keys("k", 5000):
        a.add(x); b.add(x); c.add(x)
    same = all(x._bits == y._bits for x, y in zip(a._layers, b._layers))
    diff = any(x._bits != y._bits for x, y in zip(a._layers, c._layers))
    assert same and diff
    rows.append(("种子可复现", "同种子位图一致, 异种子位图不同 -> True"))
    report("可扩展布隆过滤器（初始1万, 目标1%, 实际插入10万）", rows)


def test_counting_delete():
    cbf = CountingBloomFilter(capacity=10_000, error_rate=0.01, seed=SEED)
    items = make_keys("pos", 10_000)
    for x in items:
        cbf.add(x)
    removed = items[:5000]
    for x in removed:
        assert cbf.discard(x)
    lingering = sum(1 for x in removed if x in cbf)
    check_no_false_negatives(cbf, items[5000:])
    report("计数布隆过滤器（删除替代方案）", [
        ("已删元素仍判存在", f"{lingering}/5000 (计数器碰撞的残留误判, 可接受)"),
        ("剩余元素假阴性", "0 (删除不产生假阴性)"),
        ("内存代价", f"{cbf.nbytes} B ≈ 基础版的 8 倍"),
    ])


def benchmark():
    n = 300_000
    p = 0.001
    items = make_keys("pos", n)
    negatives = make_keys("neg", n)

    bf = BloomFilter(capacity=n, error_rate=p, seed=SEED)
    t0 = time.perf_counter()
    for x in items:
        bf.add(x)
    add_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    hits = sum(1 for x in items if x in bf)
    pos_t = time.perf_counter() - t0
    assert hits == n

    t0 = time.perf_counter()
    fp = sum(1 for x in negatives if x in bf)
    neg_t = time.perf_counter() - t0

    exact = set(items)
    t0 = time.perf_counter()
    for x in negatives:
        _ = x in exact
    exact_t = time.perf_counter() - t0
    exact_bytes = sys.getsizeof(exact) + sum(sys.getsizeof(x) for x in items)

    report(f"内存与吞吐（n={n}, p=0.1%）", [
        ("布隆过滤器内存", f"{bf.nbytes} B ({bf.nbytes/n:.2f} B/元素, "
                           f"{bf.num_bits} bits, k={bf.num_hashes})"),
        ("精确集合内存", f"≈{exact_bytes} B ({exact_bytes/n:.2f} B/元素)"),
        ("内存比", f"精确集合 / 布隆 ≈ {exact_bytes/bf.nbytes:.1f}x"),
        ("写入吞吐", f"{n/add_t:,.0f} ops/s"),
        ("查询吞吐(存在)", f"{n/pos_t:,.0f} ops/s"),
        ("查询吞吐(不存在)", f"{n/neg_t:,.0f} ops/s  (实测误判 {fp/n:.4%})"),
        ("精确集合查询吞吐", f"{n/exact_t:,.0f} ops/s (对比基准)"),
    ])


def main():
    print(f"hash 参数自检: m(1e5, 0.1%)={optimal_num_bits(100000, 0.001)} bits, "
          f"k={optimal_num_hashes(optimal_num_bits(100000, 0.001), 100000)}")
    test_empty_and_single()
    test_target_rates()
    test_overfill()
    test_scalable()
    test_counting_delete()
    benchmark()
    print("\n全部断言通过 ✔")


if __name__ == "__main__":
    main()
