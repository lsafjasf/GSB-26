"""密度曲线基准：固定 universe，扫描密度，比较两种表示的内存与耗时。

输出 density_curve.csv 并在 stdout 打印汇总与交叉点分析。
"""

import csv
import random
import sys
import time

sys.path.insert(0, ".")
from bitset_algebra import BitSet, BYTES_PER_INTERVAL

U = 1 << 20  # universe = 1,048,576
REPEAT = 5


def interval_mem(bs: BitSet) -> int:
    ivs = bs.as_representation("interval")._ivs
    mem = sys.getsizeof(ivs)
    for lo, hi in ivs:
        mem += sys.getsizeof((lo, hi)) + sys.getsizeof(lo) + sys.getsizeof(hi)
    return mem


def bitmap_mem(bs: BitSet) -> int:
    return sys.getsizeof(bs.as_representation("bitmap")._bits)


def best_time(fn, repeat=REPEAT):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    rng = random.Random(1)
    densities = [10 ** e for e in [x / 4 for x in range(-24, 1)]]  # 1e-6 .. 1.0
    rows = []
    for d in densities:
        n = min(U, max(1, int(U * d)))
        a = set(rng.sample(range(U), n))
        b = set(rng.sample(range(U), n))
        iv_a = BitSet.from_ints(a, universe=U).as_representation("interval")
        iv_b = BitSet.from_ints(b, universe=U).as_representation("interval")
        bm_a = iv_a.as_representation("bitmap")
        bm_b = iv_b.as_representation("bitmap")

        row = {
            "density": n / U,
            "count": n,
            "runs": len(iv_a._ivs),
            "mem_bitmap_B": bitmap_mem(bm_a),
            "mem_interval_B": interval_mem(iv_a),
            "t_bitmap_union_ms": best_time(lambda: bm_a.union(bm_b)) * 1e3,
            "t_interval_union_ms": best_time(lambda: iv_a.union(iv_b)) * 1e3,
            "t_bitmap_inter_ms": best_time(lambda: bm_a.intersection(bm_b)) * 1e3,
            "t_interval_inter_ms": best_time(lambda: iv_a.intersection(iv_b)) * 1e3,
        }
        rows.append(row)
        print(f"d={row['density']:.2e} runs={row['runs']:>7} "
              f"mem bm={row['mem_bitmap_B']/1024:8.1f}KiB iv={row['mem_interval_B']/1024:8.1f}KiB "
              f"union bm={row['t_bitmap_union_ms']:7.3f}ms iv={row['t_interval_union_ms']:8.3f}ms")

    with open("density_curve.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def crossover(key_bm, key_iv):
        for r in rows:
            if r[key_bm] <= r[key_iv]:
                return r["density"]
        return None

    cx_mem = crossover("mem_bitmap_B", "mem_interval_B")
    cx_union = crossover("t_bitmap_union_ms", "t_interval_union_ms")
    cx_inter = crossover("t_bitmap_inter_ms", "t_interval_inter_ms")
    print(f"\nuniverse = {U}")
    print(f"内存交叉点（bitmap 开始更省）: density ~= {cx_mem:.2e}" if cx_mem else "无内存交叉点")
    print(f"union 耗时交叉点: density ~= {cx_union:.2e}" if cx_union else "bitmap union 全程不慢")
    print(f"intersection 耗时交叉点: density ~= {cx_inter:.2e}" if cx_inter else "bitmap inter 全程不慢")
    print("数据已写入 density_curve.csv")


if __name__ == "__main__":
    main()
