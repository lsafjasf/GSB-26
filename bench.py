"""并行度耗时基准：同一批分片、同一批查询，比较 workers=1..N 的耗时。

每次运行都先做正确性对拍（并行结果必须等于串行），再计时。
计时只覆盖"扫描+聚合/过滤+合并+排序"，不含数据生成。
每个配置重复 REPEATS 次取中位数；每轮轮换配置顺序以抵消缓存/调度偏差。
进程池每次新建（真实端到端成本，含 worker 启动），故单独列出说明。

用法:
  python3 gen_bench.py            # 先生成数据（默认 100 万行 / 16 分片）
  python3 bench.py                # 默认 workers 1,2,4,8,16
  python3 bench.py --workers 1 2 4 --repeats 5
"""

import argparse
import os
import statistics
import time

from qengine import (Plan, Schema, assert_rows_equal, list_shards,
                     run_parallel, run_serial)
from qengine import expr as E
from qengine.plan import OrderKey
from qengine.queries import EVENTS_COLS, Q_AGG_REGION, Q_AGG_USER

HERE = os.path.dirname(os.path.abspath(__file__))

# 明细查询单独定义（选择性高，避免把百万行结果本身当成负载）
Q_SELECT_HEAVY = Plan(
    filter=E.ge(E.col("amount"), E.lit(9900)),    # 约 1% 命中
    select=(("user_id", E.col("user_id")),
            ("region", E.col("region")),
            ("amount", E.col("amount")),
            ("status", E.col("status")),
            ("ts", E.col("ts"))),
)

Q_ORDER_HEAVY = Plan(
    filter=E.notnull(E.col("score")),
    select=(("user_id", E.col("user_id")),
            ("region", E.col("region")),
            ("score", E.col("score")),
            ("ts", E.col("ts"))),
    order_by=(OrderKey(E.col("score"), desc=True),
              OrderKey(E.col("ts"), desc=False)),
    limit=50,
)

QUERIES = {
    "agg_region (count/sum/min/max/avg/distinct)": Q_AGG_REGION,
    "agg_user_skew (5 万分组, 热点倾斜)": Q_AGG_USER,
    "select_filter (~1% 命中, 明细保序)": Q_SELECT_HEAVY,
    "order_top50 (全分片排序+截断)": Q_ORDER_HEAVY,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(HERE, "data", "bench"))
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    shards = list_shards(args.dir)
    if not shards:
        raise SystemExit(f"no shards in {args.dir}; run: python3 gen_bench.py")
    schema = Schema(EVENTS_COLS)
    manifest = os.path.join(args.dir, "manifest.txt")
    meta = open(manifest).read().strip().replace("\n", ", ") if os.path.exists(
        manifest) else "?"
    print(f"data: {len(shards)} shards in {args.dir} ({meta})")
    print(f"workers={args.workers}, repeats={args.repeats} (median)\n")

    lines = []
    for qname, plan in QUERIES.items():
        serial = run_serial(shards, schema, plan)
        print(f"== {qname}  (结果 {len(serial)} 行)")
        results = {w: [] for w in args.workers}
        for rnd in range(args.repeats):
            order = args.workers[rnd % len(args.workers):] + \
                    args.workers[:rnd % len(args.workers)]  # 轮换
            for w in order:
                t0 = time.perf_counter()
                got = run_parallel(shards, schema, plan, w)
                dt = time.perf_counter() - t0
                assert_rows_equal(serial, got)   # 正确性闸门：不等就抛错
                results[w].append(dt)
        med = {w: statistics.median(ts) for w, ts in results.items()}
        base = med[args.workers[0]]
        print(f"  {'workers':>8} | {'median s':>9} | {'speedup':>7} | min..max")
        for w in args.workers:
            ts = results[w]
            sp = base / med[w] if med[w] else float("nan")
            print(f"  {w:>8} | {med[w]:9.3f} | {sp:6.2f}x | "
                  f"{min(ts):.3f}..{max(ts):.3f}")
            lines.append(f"{qname},w={w},median={med[w]:.3f},"
                         f"speedup={sp:.2f},min={min(ts):.3f},max={max(ts):.3f}")
        print()

    out = os.path.join(HERE, "bench_results.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# {meta}; repeats={args.repeats}; median seconds\n")
        f.write("# " + " ".join(f"workers={w}" for w in args.workers) + "\n")
        f.write("\n".join(lines) + "\n")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
