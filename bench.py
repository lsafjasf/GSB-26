"""MVCC 性能与内存基准：10 万次事务操作。

运行：python3 bench.py

场景
----
A. 混合负载 + 周期 GC：80% 单键读事务 / 20% 单键写事务，每 1000 次 GC 一次。
B. 混合负载 + 长事务钉住快照：同样负载，但一个只读长事务全程存活，
   GC 无法回收 -> 观察版本链长度与内存的增长。
C. 长事务结束后 GC：展示回收恢复。

指标：墙钟耗时、tracemalloc 内存峰值、版本总数、最大链长。
"""

import random
import time
import tracemalloc

from mvcc import MVCCStore

N_OPS = 100_000
N_KEYS = 1_000
WRITE_RATIO = 0.2
GC_EVERY = 1_000


def run_workload(with_long_reader: bool, gc: bool, label: str):
    store = MVCCStore()
    rng = random.Random(42)

    long_reader = store.begin() if with_long_reader else None

    tracemalloc.start()
    t0 = time.perf_counter()
    for i in range(N_OPS):
        key = rng.randrange(N_KEYS)
        if rng.random() < WRITE_RATIO:
            tx = store.begin()
            tx.put(key, i)
            tx.commit()
        else:
            tx = store.begin()
            tx.get(key)
            tx.rollback()
        if gc and (i + 1) % GC_EVERY == 0:
            store.gc()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    lengths = store.chain_lengths()
    max_chain = max(lengths.values()) if lengths else 0
    total = store.total_versions()

    if long_reader is not None:
        long_reader.rollback()
        if gc:
            store.gc()
        after_gc = store.total_versions()
    else:
        after_gc = total

    print(f"[{label}]")
    print(f"  操作数          : {N_OPS:,} (写 {WRITE_RATIO:.0%} / 读 {1-WRITE_RATIO:.0%})")
    print(f"  耗时            : {elapsed:.3f} s  ({N_OPS/elapsed:,.0f} ops/s)")
    print(f"  内存峰值        : {peak/1024/1024:.2f} MiB (tracemalloc)")
    print(f"  结束时版本总数  : {total:,}")
    print(f"  最大版本链长度  : {max_chain:,}")
    if long_reader is not None:
        print(f"  长事务结束+GC 后: {after_gc:,} 个版本")
    print()
    return elapsed, peak, max_chain


def run_chain_length_impact():
    """版本链长度对旧快照读延迟的影响：链越长，旧快照定位版本越慢。"""
    print("[D. 版本链长度 vs 旧快照读延迟]")
    print(f"  {'链长':>8}  {'10k 次旧快照读耗时':>18}")
    for writes in (1_000, 10_000, 50_000, 100_000):
        store = MVCCStore()
        tx = store.begin()
        tx.put("hot", 0)
        tx.commit()
        old_reader = store.begin()          # 快照钉在 v1
        for i in range(1, writes):
            t = store.begin()
            t.put("hot", i)
            t.commit()
        store.gc()                          # 被长事务阻塞，链完整保留
        chain = store.chain_length("hot")
        t0 = time.perf_counter()
        for _ in range(10_000):
            old_reader.get("hot")
        elapsed = time.perf_counter() - t0
        print(f"  {chain:>8,}  {elapsed*1000:>15.1f} ms")
        old_reader.rollback()
    print()


if __name__ == "__main__":
    print(f"Python MVCC 基准：{N_OPS:,} 次事务操作，{N_KEYS} 个键\n")
    run_workload(with_long_reader=False, gc=True,
                 label="A. 混合负载 + 周期 GC（无长事务）")
    run_workload(with_long_reader=True, gc=True,
                 label="B. 混合负载 + 只读长事务钉住快照（GC 被阻塞）")
    run_workload(with_long_reader=False, gc=False,
                 label="C. 混合负载 + 不做 GC（版本链无限增长）")
    run_chain_length_impact()
