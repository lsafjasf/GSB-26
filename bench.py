"""性能基准：10 万次事务操作下的耗时与内存峰值，以及版本链长度的影响。

运行：python3 bench.py
"""

import gc
import resource
import time
import tracemalloc

from mvcc import Store

OPS = 100_000
KEY_SPACE = 1_000  # 热点 key 空间，制造版本链


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run(ops, gc_interval, long_reader=False):
    """混合负载：~70% 单事务读、~30% 单事务写（覆盖同一热点 key 空间）。"""
    gc.collect()
    store = Store()
    seed = 12345

    def rnd():
        nonlocal seed
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        return seed

    reader = store.begin(read_only=True) if long_reader else None
    tracemalloc.start()
    t0 = time.perf_counter()
    for i in range(ops):
        key = f"k{rnd() % KEY_SPACE}"
        if rnd() % 10 < 7:
            txn = store.begin(read_only=True)
            txn.get(key)
            txn.commit()
        else:
            txn = store.begin()
            txn.put(key, i)
            txn.commit()
        if gc_interval and i % gc_interval == 0:
            store.collect_garbage()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    keys, total, max_chain, avg_chain = store.version_stats()
    if reader is not None:
        reader.commit()
    return {
        "elapsed_s": elapsed,
        "peak_tracemalloc_mb": peak / 1024 / 1024,
        "rss_mb": rss_mb(),
        "versions": total,
        "max_chain": max_chain,
        "avg_chain": avg_chain,
    }


def report(name, r):
    print(
        f"{name:<34} 耗时 {r['elapsed_s']:6.2f}s  "
        f"峰值内存(tracemalloc) {r['peak_tracemalloc_mb']:7.2f}MB  "
        f"RSS峰值 {r['rss_mb']:7.1f}MB  "
        f"残留版本 {r['versions']:>7,}  链长 max={r['max_chain']} avg={r['avg_chain']:.1f}"
    )


def main():
    print(f"负载: {OPS:,} 次事务操作 (70% 读 / 30% 写), 热点 key 空间 {KEY_SPACE}\n")

    report("定期GC (每1000次操作)", run(OPS, gc_interval=1000))
    report("不GC", run(OPS, gc_interval=0))
    report("长事务存活 + 定期GC(被阻止)", run(OPS, gc_interval=1000, long_reader=True))

    print("\n版本链长度影响 (纯写压测同一批热点 key, 不GC, 测读放大):")
    for writes in (1_000, 10_000, 50_000):
        store = Store()
        t0 = time.perf_counter()
        for i in range(writes):
            txn = store.begin()
            txn.put("hot", i)
            txn.commit()
        t_write = time.perf_counter() - t0
        _, total, max_chain, _ = store.version_stats()
        # 最新快照读只命中链头，O(1)；旧快照读需沿链扫描
        old = store.begin()
        old.snapshot_ts = 0  # 模拟一个极旧的快照
        t0 = time.perf_counter()
        for _ in range(100):
            old.get("hot")
        t_stale = time.perf_counter() - t0
        old.state = "aborted"
        store._finish(old)
        print(
            f"  链长 {max_chain:>6,}: 写 {writes:>6,} 次耗时 {t_write:5.2f}s, "
            f"旧快照读 100 次耗时 {t_stale*1000:7.2f}ms (沿链扫描)"
        )


if __name__ == "__main__":
    main()
