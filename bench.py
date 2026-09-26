"""基准测试：覆盖单块、跨块、全时间范围、极高/极低命中率、top-N 提前终止。

运行：python3 bench.py
"""

import random
import time

from logq import LogStore

N_RECORDS = 200_000
CHUNK_SIZE = 2_000


def build_store():
    rng = random.Random(42)
    store = LogStore(chunk_size=CHUNK_SIZE)
    base = 1_700_000_000
    ts = base
    recs = []
    for i in range(N_RECORDS):
        ts += rng.randint(0, 3)
        recs.append({
            "ts": ts,
            # 命中率极高：99% 为 INFO
            "level": "INFO" if rng.random() < 0.99 else rng.choice(["WARN", "ERROR"]),
            # 命中率极低：仅 0.1% 为 599
            "status": 599 if rng.random() < 0.001 else rng.choice([200, 200, 200, 301, 404, 500]),
            "latency": round(rng.uniform(0, 2000), 2),
            "service": rng.choice(["api", "web", "worker", "cron"]),
            "user_id": rng.randint(1, 500_000),
        })
    store.ingest(recs)
    store.finalize()
    return store, base, ts


def run(store, name, q, repeat=3):
    # 预热后取最优值，减少抖动
    best = None
    for _ in range(repeat):
        r = store.query(q)
        if best is None or r.elapsed_ms < best.elapsed_ms:
            best = r
    full = store.full_scan(q)
    # 对拍校验
    assert best.rows == full.rows and best.counts == full.counts, f"结果不一致: {q}"
    n = len(store.chunks)
    if best.counts is not None:
        size = f"{sum(best.counts.values())} 行/{len(best.counts)} 组"
    else:
        size = f"{len(best.rows)} 行"
    print(f"| {name} | `{q}` | {best.scanned_chunks}/{n} | {best.skipped_chunks}/{n} "
          f"| {best.elapsed_ms:8.2f} | {full.elapsed_ms:8.2f} | {size} |")
    return best


def main():
    t0 = time.perf_counter()
    store, base, ts_max = build_store()
    build_ms = (time.perf_counter() - t0) * 1000
    n = len(store.chunks)
    print(f"数据集: {N_RECORDS} 条记录, {n} 个块 (每块 {CHUNK_SIZE} 条), "
          f"构建耗时 {build_ms:.0f} ms")
    print(f"时间范围: [{base}, {ts_max}]")
    print()
    print("| 场景 | 查询 | 扫描块 | 跳过块 | 剪枝查询 ms | 全量扫描 ms | 结果规模 |")
    print("|---|---|---|---|---|---|---|")

    span = ts_max - base
    # 1. 单块：时间范围落在某一个块内
    one_block_lo = base + span // 2
    run(store, "单块（时间范围落在 1 个块内）",
        f"ts>={one_block_lo} AND ts<={one_block_lo + 200}")
    # 2. 跨块：时间范围跨越约 10% 的块
    run(store, "跨块（时间范围跨约 10% 的块）",
        f"ts>={base + span // 4} AND ts<={base + span // 4 + span // 10}")
    # 3. 时间范围覆盖全部数据：无法剪枝
    run(store, "时间范围覆盖全部数据",
        f"ts>={base} AND ts<={ts_max}")
    # 4. 字段命中率极高（99%）
    run(store, "字段命中率极高 level=INFO (99%)", 'level="INFO"')
    # 5. 字段命中率极低（0.1%），取值集合直接排除全部块
    run(store, "字段命中率极低 status=599 (0.1%)", "status=599")
    # 5b. 不存在的值：零扫描
    run(store, "字段值不存在 status=418 (0%)", "status=418")
    # 6. 组合：时间范围 + 字段条件
    run(store, "组合：时间范围 + 字段等值 + 数值范围",
        f"ts>={base + span // 3} AND ts<={base + 2 * span // 3} "
        f'AND service="api" AND latency>=1500')
    # 7. 分组计数聚合
    run(store, "分组计数 count by level", 'status=500 | count by level')
    # 8. top N 提前终止
    run(store, "top 10（按 ts 倒序，提前终止）", "latency>=0 | top 10")
    run(store, "top 100 + 字段条件", 'service="api" | top 100')


if __name__ == "__main__":
    main()
