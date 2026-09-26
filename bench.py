"""基准：扫描量与耗时。运行：python3 bench.py

数据集：200,000 条记录，20 个块 × 10,000 条，ts 每秒一条。
字段分布经过设计，覆盖命中率极高/极低、单块/跨块/全量等情形。
输出 Markdown 表格，可直接贴入文档。
"""

import time

from logengine import Store, execute, full_scan, parse

BASE_TS = 1_700_000_000
BLOCKS = 20
PER_BLOCK = 10_000
SERVICES = ["auth", "api", "web", "worker", "db"]


def build_store() -> Store:
    store = Store()
    for b in range(BLOCKS):
        for j in range(PER_BLOCK):
            i = b * PER_BLOCK + j
            store.append({
                "ts": BASE_TS + i,
                "service": SERVICES[b // 4],          # 每个服务占 4 个连续块
                "level": "error" if i % 100 == 0 else ("warn" if i % 100 < 10 else "info"),
                "latency": b * 50 + (i % 50),         # 每块一个 50ms 区间段
                "user_id": i,                          # 唯一，块 b 含 [b*10000, b*10000+9999]
                "env": "prod",                         # 常量，命中率 100%
                "ok": i % 2 == 0,
            })
        store.seal_block()
    return store


QUERIES = [
    ("时间范围·单块",
     f"SELECT * WHERE ts >= {BASE_TS + 71234} AND ts <= {BASE_TS + 72345}"),
    ("时间范围·跨5块",
     f"SELECT * WHERE ts >= {BASE_TS + 25000} AND ts < {BASE_TS + 70000}"),
    ("时间范围·覆盖全部",
     "SELECT * WHERE ts >= 0"),
    ("等值·命中率极低(1/20万)",
     "SELECT * WHERE user_id = 123456"),
    ("等值·命中率极高(100%)",
     "SELECT * WHERE env = 'prod'"),
    ("等值·无匹配",
     "SELECT * WHERE service = 'nope'"),
    ("数值范围·跨2块",
     "SELECT * WHERE latency >= 160 AND latency < 210"),
    ("分组计数·全量",
     "SELECT COUNT(*) WHERE ts >= 0 GROUP BY service"),
    ("Top10·倒序提前终止",
     "SELECT * WHERE ts >= 0 ORDER BY ts DESC LIMIT 10"),
    ("Top5·带过滤提前终止",
     "SELECT * WHERE service = 'web' ORDER BY ts DESC LIMIT 5"),
    ("空结果·时间在未来",
     f"SELECT * WHERE ts >= {BASE_TS + 999_999_999}"),
]


def best_time(fn, repeats=5):
    best = float("inf")
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return result, best


def matches(data):
    if isinstance(data, int):
        return data
    if isinstance(data, list) and data and isinstance(data[0], tuple):
        return sum(c for _, c in data)
    return len(data)


def main():
    t0 = time.perf_counter()
    store = build_store()
    build_s = time.perf_counter() - t0
    print(f"数据集：{store.total_rows()} 条记录，{len(store.blocks)} 个块"
          f"（每块 {PER_BLOCK} 条），构建耗时 {build_s:.2f}s\n")
    header = ("| 场景 | 扫描块 | 跳过块(下推/限流) | 扫描记录 | 命中 | "
              "引擎耗时ms | 全扫耗时ms | 加速比 |")
    print(header)
    print("|" + "---|" * 8)
    for name, sql in QUERIES:
        q = parse(sql)
        fast, t_fast = best_time(lambda: execute(store, q))
        slow, t_slow = best_time(lambda: full_scan(store, q))
        assert fast.data == slow.data, f"对拍失败: {name}"
        m = fast.metrics
        speedup = t_slow / t_fast if t_fast > 0 else float("inf")
        print(f"| {name} | {m.scanned_blocks}/{m.total_blocks} "
              f"| {m.pruned_blocks}/{m.skipped_limit_blocks} "
              f"| {m.scanned_records} | {matches(fast.data)} "
              f"| {t_fast * 1000:.2f} | {t_slow * 1000:.2f} | {speedup:.1f}x |")
    print("\n说明：跳过块列 = 块级统计下推跳过 / LIMIT 提前终止未触及；"
          "每查询取 5 次运行最优耗时；全部结果已与全量扫描对拍一致。")


if __name__ == "__main__":
    main()
