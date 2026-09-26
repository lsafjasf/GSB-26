"""密度曲线基准：两种表示在不同密度 / 不同上界下的内存与耗时。

运行: python3 bench_density.py
输出: 终端表格 + density_curve.csv + upper_bound_curve.csv
"""
import csv
import random
import sys
import time

from bitsetlib import (
    bm_from_values, iv_union, iv_intersection,
    bitmap_cost, interval_cost, INTERVAL_BYTES, BITMAP_BASE_BYTES,
)


def ivs_from_sorted(vals):
    """有序去重值 -> 规范化区间，O(n) 纯 Python（避免 bigint 转换干扰计时）。"""
    ivs = []
    for v in vals:
        if ivs and v == ivs[-1][1] + 1:
            ivs[-1] = (ivs[-1][0], v)
        else:
            ivs.append((v, v))
    return ivs


def time_op(fn, min_seconds=0.05, cap=2000):
    """自适应重复计时，返回单次耗时（秒）。"""
    t0 = time.perf_counter()
    fn()
    est = time.perf_counter() - t0
    reps = max(1, min(cap, int(min_seconds / max(est, 1e-9))))
    t0 = time.perf_counter()
    for _ in range(reps):
        fn()
    return (time.perf_counter() - t0) / reps


def bitmap_mem(bits):
    return sys.getsizeof(bits)


def interval_mem(ivs):
    total = sys.getsizeof(ivs)
    for t in ivs:
        total += sys.getsizeof(t) + sys.getsizeof(t[0]) + sys.getsizeof(t[1])
    return total


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024


def fmt_time(s):
    if s < 1e-3:
        return "%.1f us" % (s * 1e6)
    if s < 1:
        return "%.2f ms" % (s * 1e3)
    return "%.3f s" % s


def bench_density(upper=1 << 20):
    densities = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0]
    rng = random.Random(2026)
    rows = []
    for d in densities:
        n = max(1, int(upper * d))
        a = sorted(rng.sample(range(upper), n))
        b = sorted(rng.sample(range(upper), n))

        bits_a, bits_b = bm_from_values(a, upper), bm_from_values(b, upper)
        ivs_a, ivs_b = ivs_from_sorted(a), ivs_from_sorted(b)
        k = len(ivs_a)

        mem_bm = bitmap_mem(bits_a)
        mem_iv = interval_mem(ivs_a)

        t_bm_union = time_op(lambda: bits_a | bits_b)
        t_iv_union = time_op(lambda: iv_union(ivs_a, ivs_b))
        t_bm_and = time_op(lambda: bits_a & bits_b)
        t_iv_and = time_op(lambda: iv_intersection(ivs_a, ivs_b))

        rows.append(dict(density=d, n=n, k=k,
                         mem_bitmap=mem_bm, mem_interval=mem_iv,
                         t_bm_union=t_bm_union, t_iv_union=t_iv_union,
                         t_bm_and=t_bm_and, t_iv_and=t_iv_and))

        # 实测每区间字节数，校准模型常量
        per_iv = (mem_iv - sys.getsizeof(ivs_a)) / max(k, 1)
        print("d=%-8g n=%-8d k=%-8d | mem bm=%-10s iv=%-10s | "
              "union bm=%-9s iv=%-9s | and bm=%-9s iv=%-9s | iv字节/区间=%.0f"
              % (d, n, k, fmt_bytes(mem_bm), fmt_bytes(mem_iv),
                 fmt_time(t_bm_union), fmt_time(t_iv_union),
                 fmt_time(t_bm_and), fmt_time(t_iv_and), per_iv))

    with open("density_curve.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 沿密度上升方向找第一个“区间不再占优”的点（d=1 时 k=1 区间重新占优，单列说明）
    mem_cross = next((r["density"] for r in rows
                      if r["mem_interval"] >= r["mem_bitmap"]), None)
    time_cross = next((r["density"] for r in rows
                       if r["t_iv_union"] >= r["t_bm_union"]), None)
    print("\n[交叉点] 随机数据下：密度 >= %s 区间内存更亏；密度 >= %s 区间并集更慢"
          % (mem_cross, time_cross))
    print("[模型] 理论内存交叉: k * %d B = U/8 + %d B  ->  k/U = 1/%d = %.2e"
          % (INTERVAL_BYTES, BITMAP_BASE_BYTES, 8 * INTERVAL_BYTES,
             1.0 / (8 * INTERVAL_BYTES)))
    print("[注] d=1.0 时全集只有 1 个区间，区间表示重新占优（两端稀疏性对称）。")
    return rows


def bench_upper_bound(density=0.01):
    print("\n固定密度 %.3f，扫描上界 U（位图随 U 线性增长，区间与 U 无关）:" % density)
    rng = random.Random(7)
    rows = []
    for exp in range(14, 25, 2):
        upper = 1 << exp
        n = max(1, int(upper * density))
        a = sorted(rng.sample(range(upper), n))
        bits = bm_from_values(a, upper)
        ivs = ivs_from_sorted(a)
        mem_bm, mem_iv = bitmap_mem(bits), interval_mem(ivs)
        t_bm = time_op(lambda: bits & bits)
        t_iv = time_op(lambda: iv_intersection(ivs, ivs))
        rows.append(dict(upper=upper, n=n, k=len(ivs),
                         mem_bitmap=mem_bm, mem_interval=mem_iv,
                         t_bm_and=t_bm, t_iv_and=t_iv))
        print("U=2^%-2d n=%-8d k=%-8d | mem bm=%-10s iv=%-10s | and bm=%-9s iv=%s"
              % (exp, n, len(ivs), fmt_bytes(mem_bm), fmt_bytes(mem_iv),
                 fmt_time(t_bm), fmt_time(t_iv)))
    with open("upper_bound_curve.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 超大稀疏：位图不可行
    U_huge, n_huge = 1 << 40, 1000
    print("\n超大稀疏: U=2^40, n=%d -> 位图理论需 %s，区间实测约 %s"
          % (n_huge, fmt_bytes(bitmap_cost(U_huge)),
             fmt_bytes(interval_cost(n_huge))))


if __name__ == "__main__":
    print("=== 密度曲线 (U = 2^20 = %d) ===" % (1 << 20))
    bench_density()
    bench_upper_bound()
