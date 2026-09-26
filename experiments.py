"""实验：命中率 / 平均读延迟 / 磁盘读写量对比 + 热点注入收敛实验。

运行：python3 experiments.py
"""

import random
import time

from tiered_store import TieredStore


def make_value(i, size=256):
    return (f"value-{i}-".encode() * (size // 8 + 1))[:size]


def zipf_workload(rng, n_keys, n_reads, alpha=1.2):
    weights = [1.0 / (i + 1) ** alpha for i in range(n_keys)]
    keys = [f"k{i:05d}" for i in range(n_keys)]
    return rng.choices(keys, weights=weights, k=n_reads)


def run_store(capacity, n_keys, reads, **kw):
    store = TieredStore(capacity, **kw)
    for i in range(n_keys):
        store.put(f"k{i:05d}", make_value(i))
    m0 = store.metrics
    base = m0.snapshot()
    for k in reads:
        store.get(k)
    snap = m0.snapshot()
    result = {k: snap[k] - base[k] if isinstance(snap[k], (int, float)) else snap[k]
              for k in snap}
    result["hit_rate"] = round(m0.hot_hits and (m0.hot_hits - 0) / max(1, len(reads)), 4)
    # 重新精确计算本阶段指标
    store.close()
    return result


def phase_metrics(store, reads):
    """跑一段读负载，返回该阶段的增量指标。"""
    m = store.metrics
    h0, c0, mi0 = m.hot_hits, m.cold_hits, m.misses
    dr0, dw0 = m.disk_reads, m.disk_writes
    drb0, dwb0 = m.disk_read_bytes, m.disk_write_bytes
    lat0, ops0 = m.read_latency_sum, m.read_ops
    for k in reads:
        store.get(k)
    n = len(reads)
    return {
        "hit_rate": (m.hot_hits - h0) / n,
        "avg_lat_us": (m.read_latency_sum - lat0) / max(1, m.read_ops - ops0) * 1e6,
        "disk_reads": m.disk_reads - dr0,
        "disk_writes": m.disk_writes - dw0,
        "disk_read_kb": (m.disk_read_bytes - drb0) / 1024,
        "disk_write_kb": (m.disk_write_bytes - dwb0) / 1024,
    }


# ---------------------------------------------------------------- 实验 1

def exp_policy_compare():
    print("=" * 96)
    print("实验 1：淘汰策略对比（Zipf 倾斜负载，3000 key x 256B = 750KB，热层容量 64KB 约 8.5%）")
    print("=" * 96)
    rng = random.Random(42)
    n_keys, n_reads, cap = 3000, 30000, 64 * 1024
    reads = zipf_workload(rng, n_keys, n_reads)

    configs = [
        ("LRU（仅最近时间）",      dict(freq_weight=0.0, recency_weight=1.0)),
        ("LFU（仅频次）",          dict(freq_weight=1.0, recency_weight=0.0)),
        ("组合 频次+最近（默认）", dict(freq_weight=1.0, recency_weight=1.0)),
        ("组合 + 预取",            dict(freq_weight=1.0, recency_weight=1.0,
                                     prefetch=True, prefetch_count=4)),
        ("组合 + 提前下沉",        dict(freq_weight=1.0, recency_weight=1.0,
                                     early_sink=True, sink_score_threshold=1.0)),
    ]
    header = f"{'策略':<22}{'热层命中率':>10}{'平均读延迟':>12}{'磁盘读次数':>12}{'磁盘写次数':>12}{'磁盘读KB':>10}{'磁盘写KB':>10}"
    print(header)
    print("-" * 96)
    for name, kw in configs:
        store = TieredStore(cap, **kw)
        for i in range(n_keys):
            store.put(f"k{i:05d}", make_value(i))
        r = phase_metrics(store, reads)
        store.close()
        print(f"{name:<22}{r['hit_rate']:>10.2%}{r['avg_lat_us']:>10.2f}us"
              f"{r['disk_reads']:>12}{r['disk_writes']:>12}"
              f"{r['disk_read_kb']:>10.0f}{r['disk_write_kb']:>10.0f}")
    print("注：预取会把后续访问的磁盘读提前，磁盘读次数含预取；提前下沉用磁盘写换内存余量。")


# ---------------------------------------------------------------- 实验 2

def exp_prefetch_sequential():
    print()
    print("=" * 96)
    print("实验 2：预取开关效果（顺序扫描负载，2000 key x 256B，热层容量 51KB 约 10%，扫描 3 遍）")
    print("=" * 96)
    n_keys, cap = 2000, 51 * 1024
    keys = [f"k{i:05d}" for i in range(n_keys)]
    header = f"{'配置':<24}{'热层命中率':>10}{'平均读延迟':>12}{'磁盘读次数':>12}{'磁盘读KB':>10}"
    print(header)
    print("-" * 96)
    for name, kw in [("预取 关", dict(prefetch=False)),
                     ("预取 开（8条/次）", dict(prefetch=True, prefetch_count=8))]:
        store = TieredStore(cap, **kw)
        for i in range(n_keys):
            store.put(f"k{i:05d}", make_value(i))
        reads = keys * 3
        r = phase_metrics(store, reads)
        store.close()
        print(f"{name:<24}{r['hit_rate']:>10.2%}{r['avg_lat_us']:>10.2f}us"
              f"{r['disk_reads']:>12}{r['disk_read_kb']:>10.0f}")


# ---------------------------------------------------------------- 实验 3

def exp_hotspot_convergence():
    print()
    print("=" * 96)
    print("实验 3：突发热点注入后的收敛时间（5000 key，热层容量 64KB 约 250 条）")
    print("        阶段一：热点 A（400 key，超过热层容量）占 80% 访问，持续 10000 次读；")
    print("        阶段二：热点 A 消亡，注入新热点 B（50 key）占 80% 访问，持续 20000 次读；")
    print("        收敛 = 热点 B 的最近 200 次访问中热层驻留比例 >= 95%")
    print("=" * 96)
    rng = random.Random(7)
    n_keys, cap = 5000, 64 * 1024  # 64KB / (5000*256B) = 5%
    sample = rng.sample(range(n_keys), 450)
    hotspot_a = [f"k{i:05d}" for i in sample[:400]]
    hotspot_b = [f"k{i:05d}" for i in sample[400:]]

    def mixed(hotspot, n):
        return [rng.choice(hotspot) if rng.random() < 0.8 else f"k{rng.randrange(n_keys):05d}"
                for _ in range(n)]

    phase1_reads = mixed(hotspot_a, 10000)
    phase2_reads = mixed(hotspot_b, 20000)

    configs = [
        ("LRU",            dict(freq_weight=0.0, recency_weight=1.0)),
        ("LFU 无衰减",     dict(freq_weight=1.0, recency_weight=0.0, decay_interval=10 ** 9)),
        ("LFU 衰减",       dict(freq_weight=1.0, recency_weight=0.0, decay_interval=500)),
        ("组合 频次+最近", dict(freq_weight=1.0, recency_weight=1.0, decay_interval=500)),
    ]
    header = (f"{'策略':<18}{'收敛所需读次数':>14}{'收敛耗时':>12}"
              f"{'热点命中率(后2000次)':>20}{'全程热层命中率':>14}")
    print(header)
    print("-" * 96)
    for name, kw in configs:
        store = TieredStore(cap, **kw)
        for i in range(n_keys):
            store.put(f"k{i:05d}", make_value(i))
        for k in phase1_reads:
            store.get(k)

        hotset = set(hotspot_b)
        converge_at, converge_time = None, None
        t0 = time.perf_counter()
        window = []
        for idx, k in enumerate(phase2_reads):
            store.get(k)
            if k in hotset:
                window.append(1 if k in store._hot else 0)
                if len(window) > 200:
                    window.pop(0)
                if converge_at is None and len(window) == 200 and sum(window) / 200 >= 0.95:
                    converge_at = idx + 1
                    converge_time = time.perf_counter() - t0

        # 后 2000 次热点读命中率
        tail = phase2_reads[-2000:]
        m = store.metrics
        h0 = m.hot_hits
        hs_reads = [k for k in tail if k in hotset]
        for k in hs_reads:
            store.get(k)
        hs_hit = (m.hot_hits - h0) / max(1, len(hs_reads))
        overall = m.hot_hits / m.total_reads
        store.close()
        conv = str(converge_at) if converge_at else "未收敛"
        ct = f"{converge_time * 1000:.1f}ms" if converge_time else "-"
        print(f"{name:<18}{conv:>14}{ct:>12}{hs_hit:>20.2%}{overall:>14.2%}")


if __name__ == "__main__":
    exp_policy_compare()
    exp_prefetch_sequential()
    exp_hotspot_convergence()
