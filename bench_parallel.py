"""不同并行度下串行/并行扫描耗时基准（仅标准库）。

两类负载：
- 聚合查询（小结果，跨分片聚合）——并行收益主要看这里；
- 明细查询（过滤后约 1% 行命中，结果经 pickle 回传）——含 IPC 成本。

耗时为 3 次重复的最小值（秒）。运行：python3 bench_parallel.py [N]
"""

import multiprocessing as mp
import random
import sys
import time

from query_engine import (
    Aggregate, Filter, Query, ParallelExecutor, execute_parallel,
    execute_serial,
)


def make_rows(n, seed=42):
    rnd = random.Random(seed)
    regions = ["cn", "us", "eu", "jp"]
    return [{
        "id": i,
        "amount": rnd.randint(0, 10_000),
        "region": rnd.choice(regions),
    } for i in range(n)]


AGG = Query(filter=Filter("amount", "ge", 0), aggregates=(
    Aggregate("count"), Aggregate("sum", "amount"),
    Aggregate("min", "amount"), Aggregate("max", "amount"),
    Aggregate("distinct", "region"),
))
SCAN = Query(filter=Filter("amount", "gt", 9_900),
             projection=("id", "amount", "region"))


def timed(fn, repeat=3):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    rows = make_rows(n)
    cpus = mp.cpu_count()
    levels = [p for p in (1, 2, 4, 8, 16) if p <= cpus]
    print(f"rows={n:,}  cpus={cpus}  python={sys.version.split()[0]}")
    print(f"{'query':<10} {'serial':>8}", end="")
    for p in levels:
        print(f" {'p=' + str(p):>9}", end="")
    print()

    for name, query in (("agg", AGG), ("scan(1%)", SCAN)):
        serial_t, serial_r = timed(lambda: execute_serial(rows, query))
        line = f"{name:<10} {serial_t:>8.3f}"
        # 正确性守门：所有并行结果必须等于串行
        for p in levels:
            t, r = timed(lambda p=p: execute_parallel(rows, query, workers=p))
            assert r == serial_r, f"mismatch at p={p} ({name})"
            speedup = serial_t / t
            line += f" {t:>6.3f}x{speedup:<3.1f}"
        print(line)

    print()
    print("单元格 = 最小耗时(秒) x 相对串行加速比。")
    print("说明：workers=1 时只有 1 个分片，实现直接走串行基线（无进程开销）；")
    print("      p>=2 每次查询新建 fork 进程池，含建池固定开销（页表复制）。")

    # ---- 常驻池（ParallelExecutor）：bind 一次，查询摊掉建池成本 ----
    print()
    print("常驻池模式（ParallelExecutor，bind 一次后反复查询）：")
    print(f"{'query':<10} {'serial':>8}", end="")
    warm_levels = [p for p in levels if p >= 2]
    for p in warm_levels:
        print(f" {'p=' + str(p):>9}", end="")
    print()
    for name, query in (("agg", AGG), ("scan(1%)", SCAN)):
        serial_t, serial_r = timed(lambda: execute_serial(rows, query))
        line = f"{name:<10} {serial_t:>8.3f}"
        for p in warm_levels:
            t0 = time.perf_counter()
            ex = ParallelExecutor(workers=p)
            ex.bind(rows)
            bind_cost = time.perf_counter() - t0
            t, r = timed(lambda ex=ex: ex.execute(query))
            ex.close()
            assert r == serial_r, f"mismatch at p={p} ({name}, warm)"
            line += f" {t:>6.3f}x{serial_t / t:<3.1f}"
            if name == "agg":
                print(f"# p={p} bind(建池+共享数据) 一次性成本 {bind_cost:.3f}s")
        print(line)


if __name__ == "__main__":
    main()
