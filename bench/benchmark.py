"""性能与内存基准：python3 bench/benchmark.py"""
import gc
import random
import resource
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from rollback_dsu import RollbackDSU

rss_mb = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def bench_throughput(n=1_000_000, ops=1_000_000):
    rng = random.Random(42)
    d = RollbackDSU(n)
    t0 = time.perf_counter()
    done = 0
    for _ in range(ops):
        d.union(rng.randrange(n), rng.randrange(n))
        done += 1
    dt = time.perf_counter() - t0
    print(f"[吞吐] n={n:,}，{done:,} 次随机 union：{dt:.2f}s，"
          f"{done/dt/1e6:.2f} M ops/s，日志深度={d.checkpoint():,}，"
          f"峰值 RSS={rss_mb():.1f} MB")
    return d


def bench_memory_vs_depth():
    print("\n[内存] 回滚栈（操作日志）深度 vs 内存增量（tracemalloc）")
    print(f"{'日志深度':>12} {'tracemalloc 峰值':>18} {'每条日志字节':>14}")
    n = 2_000_000
    for depth in (0, 1_000, 10_000, 100_000, 1_000_000):
        gc.collect()
        d = RollbackDSU(n)
        tracemalloc.start()
        for i in range(depth):          # 链式合并，每次必生效，日志逐条增长
            d.union(i, i + 1)
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        per = peak / depth if depth else 0
        print(f"{depth:>12,} {peak/1e6:>15.2f} MB {per:>13.1f} B")
        d.rollback(0)
        del d


def bench_mixed_million():
    n, ops = 500_000, 1_000_000
    rng = random.Random(7)
    d = RollbackDSU(n)
    cps = []
    gc.collect()
    base = rss_mb()
    t0 = time.perf_counter()
    for _ in range(ops):
        r = rng.random()
        if r < 0.55:
            d.union(rng.randrange(n), rng.randrange(n))
        elif r < 0.65:
            cps.append(d.checkpoint())
        elif r < 0.85 and cps:
            i = rng.randrange(len(cps))
            d.rollback(cps[i])
            del cps[i + 1:]
        else:
            d.find(rng.randrange(n))
    dt = time.perf_counter() - t0
    print(f"\n[混合] n={n:,}，1,000,000 次混合操作（union/checkpoint/rollback/find）："
          f"{dt:.2f}s，{ops/dt/1e6:.2f} M ops/s")
    print(f"       结束时日志深度={d.checkpoint():,}，组件数={d.num_components:,}，"
          f"峰值 RSS={rss_mb():.1f} MB（基线 {base:.1f} MB）")


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}，峰值内存取 ru_maxrss（整进程）")
    bench_throughput()
    bench_memory_vs_depth()
    bench_mixed_million()
