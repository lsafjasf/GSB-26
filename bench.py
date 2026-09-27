#!/usr/bin/env python3
"""性能与内存基准：空表、单侧列、超宽表、百万行表。

用法: python3 bench.py [场景名...]   默认跑全部
"""

import gc
import random
import resource
import sys
import time
import tracemalloc

from tablediff import Column, DiffConfig, Table, diff_tables


def make_rows(n, ncols, rng, key_start=0):
    rows = []
    for i in range(n):
        row = [key_start + i]
        for j in range(1, ncols):
            row.append(rng.random() * 1000 if j % 2 else rng.randint(0, 10**6))
        rows.append(tuple(row))
    return rows


def make_schema(ncols):
    cols = [Column("id", "int")]
    for j in range(1, ncols):
        cols.append(Column(f"f{j}", "float" if j % 2 else "int"))
    return cols


def measure(name, build):
    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    t0 = time.perf_counter()
    left, right, cfg = build()
    t1 = time.perf_counter()
    rep = diff_tables(left, right, cfg)
    t2 = time.perf_counter()
    peak, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[{name}]")
    print(f"  行数: left={rep.left_rows} right={rep.right_rows}  列数: {len(left.schema)}/{len(right.schema)}")
    print(f"  建表耗时: {t1 - t0:.3f}s   diff 耗时: {t2 - t1:.3f}s")
    print(f"  Python 堆峰值(tracemalloc): {peak / 1024**2:.1f} MiB   "
          f"进程 RSS 增量: {max(0, rss_after - rss_before) / 1024:.1f} MiB")
    print(f"  差异: +{rep.added_count} -{rep.removed_count} ~{rep.modified_count} "
          f"(字段级 {rep.field_diff_count})  占比 {rep.diff_ratio:.2%}")
    print()
    del left, right, rep
    gc.collect()


def scen_empty():
    s = make_schema(5)
    return Table(s, []), Table(s, []), DiffConfig()


def scen_one_side_column():
    rng = random.Random(1)
    n = 100000
    ls, rs = make_schema(6), make_schema(6)
    rs.append(Column("only_right", "str"))
    left = Table(ls, make_rows(n, 6, rng))
    right_rows = [r + ("v",) for r in make_rows(n, 6, rng, key_start=n // 2)]
    right = Table(rs, right_rows)
    return left, right, DiffConfig()


def scen_wide():
    rng = random.Random(2)
    ncols, n = 1000, 20000
    s = make_schema(ncols)
    left_rows = make_rows(n, ncols, rng)
    right_rows = [tuple(r) for r in left_rows]
    for i in range(0, n, 100):  # 1% 行改一个字段
        row = list(right_rows[i])
        row[1] = row[1] + 1.0
        right_rows[i] = tuple(row)
    return Table(s, left_rows), Table(s, right_rows), DiffConfig()


def scen_million():
    rng = random.Random(3)
    n = 1_000_000
    ncols = 8
    s = make_schema(ncols)
    left_rows = make_rows(n, ncols, rng)
    right_rows = list(left_rows[n // 20:])  # 删 5%
    next_key = n
    for _ in range(n // 20):  # 增 5%
        right_rows.append(tuple([next_key] + [0.5] * (ncols - 1)))
        next_key += 1
    for i in range(0, len(right_rows), 50):  # 改 2%
        row = list(right_rows[i])
        row[1] = row[1] + 0.001
        right_rows[i] = tuple(row)
    cfg = DiffConfig(tolerances={"f1": (0.0005, 0.0)}, max_samples=5, max_field_diffs=1000)
    return Table(s, left_rows), Table(s, right_rows), cfg


SCENARIOS = {
    "empty": scen_empty,
    "one-side-column": scen_one_side_column,
    "wide-1000col": scen_wide,
    "million-rows": scen_million,
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(SCENARIOS)
    for name in names:
        measure(name, SCENARIOS[name])
