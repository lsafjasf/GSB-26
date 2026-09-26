"""性能与内存基准：空表 / 单侧列 / 超宽表 / 百万行表。

运行: python3 benchmark.py
内存口径: resource.getrusage(RUSAGE_SELF).ru_maxrss（进程峰值 RSS, Linux 单位 KB），
         报告每个场景结束后相对起始的增量。
"""

import gc
import random
import resource
import time

from tablediff import Table, compare


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def bench(name, build, **cmp_kwargs):
    gc.collect()
    t0 = time.perf_counter()
    left, right, key = build()
    t1 = time.perf_counter()
    rep = compare(left, right, key=key, max_examples=100, **cmp_kwargs)
    t2 = time.perf_counter()
    rss = peak_rss_mb()
    s = rep.summary
    print(f"[{name}]")
    print(f"  规模: 左 {s['rows_left']} 行 / 右 {s['rows_right']} 行, "
          f"比较列 {len(rep.schema['compared_columns'])}")
    print(f"  差异: 新增 {s['added']} 删除 {s['deleted']} "
          f"修改行 {s['modified_rows']} 修改字段 {s['modified_fields']}")
    print(f"  耗时: 数据生成 {t1 - t0:.3f}s, 比较 {t2 - t1:.3f}s")
    print(f"  内存: 进程峰值 RSS {rss:.1f} MB")
    print()
    del left, right, rep
    gc.collect()


def sc_empty():
    cols = [("id", "int"), ("v", "float")]
    return Table("L", cols, []), Table("R", cols, []), "id"


def sc_one_sided_columns():
    rng = random.Random(1)
    n = 100_000
    lcols = [("id", "int"), ("a", "float"), ("b", "str"), ("left_only", "int")]
    rcols = [("id", "int"), ("a", "float"), ("b", "str"), ("right_only", "str")]
    common = [(i, rng.random(), f"s{i}") for i in range(n)]
    lrows = [(i, a, b, i) for i, a, b in common]
    rrows = [(i, a, b, f"r{i}") for i, a, b in common]
    return Table("L", lcols, lrows), Table("R", rcols, rrows), "id"


def sc_wide():
    rng = random.Random(2)
    ncols, nrows = 1000, 20_000
    cols = [("id", "int")] + [(f"c{i}", "float") for i in range(ncols - 1)]
    base = [tuple([i] + [rng.random()] * (ncols - 1)) for i in range(nrows)]
    right = []
    for i, row in enumerate(base):
        r = list(row)
        if i % 100 == 0:
            r[1] += 1.0  # 1% 的行有修改
        right.append(tuple(r))
    return Table("L", cols, base), Table("R", cols, right), "id"


def sc_million():
    rng = random.Random(3)
    n = 1_000_000
    cols = [("id", "int"), ("a", "float"), ("b", "float"), ("c", "str"),
            ("d", "int"), ("e", "float"), ("f", "str"), ("g", "int")]
    left = [(i, rng.random(), rng.random(), f"x{i % 997}", i % 50,
             rng.random(), f"y{i % 31}", i % 7) for i in range(n)]
    right = []
    for i, row in enumerate(left):
        r = list(row)
        if i % 100 == 0:
            r[1] += 5.0            # 1% 修改
        right.append(tuple(r))
    # 0.1% 删除（左有右无）+ 0.1% 新增（右有左无）
    right = [r for r in right if r[0] % 1000 != 0]
    right += [(n + i, 0.0, 0.0, "new", 0, 0.0, "new", 0) for i in range(1000)]
    return Table("L", cols, left), Table("R", cols, right), "id"


if __name__ == "__main__":
    print(f"起始峰值 RSS: {peak_rss_mb():.1f} MB\n")
    bench("空表 (0 行)", sc_empty)
    bench("单侧存在列 (10万行)", sc_one_sided_columns)
    bench("超宽表 (1000 列 x 2万行)", sc_wide)
    bench("百万行表 (100万行 x 8列)", sc_million)
