"""内存峰值与吞吐基准：覆盖空流、单键、全部键相同、全部键不同、重尾分布。

内存峰值用 tracemalloc 测量（独立运行以免拖慢计时）；
吞吐为纯 update 循环的墙钟时间。运行：python3 bench.py
"""

import gc
import sys
import time
import tracemalloc

from space_saving import SpaceSaving

N = 1_000_000
CAPACITY = 1_000


def make_streams(n):
    return {
        "空流 (N=0)": [],
        "单键重复": [0] * n,
        "全部键相同": ["same"] * n,
        "全部键不同": list(range(n)),
        "重尾 Zipf": None,  # 惰性生成，避免占用测量内存
    }


def gen_zipf(n):
    import random
    rng = random.Random(42)
    vocab = 10_000
    return rng.choices(range(vocab), weights=[1 / (i + 1) for i in range(vocab)], k=n)


def bench_throughput(name, stream):
    ss = SpaceSaving(CAPACITY)
    update = ss.update
    t0 = time.perf_counter()
    for x in stream:
        update(x)
    dt = time.perf_counter() - t0
    rate = len(stream) / dt if dt > 0 else float("inf")
    return dt, rate


def bench_memory(stream):
    gc.collect()
    tracemalloc.start()
    ss = SpaceSaving(CAPACITY)
    for x in stream:
        ss.update(x)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def main():
    print(f"容量 m={CAPACITY:,}，目标流长 N={N:,}\n")
    header = f"{'场景':<14} | {'N':>9} | {'耗时':>8} | {'吞吐 (项/秒)':>14} | {'内存峰值':>10}"
    print(header)
    print("-" * len(header))
    for name, stream in make_streams(N).items():
        if stream is None:
            stream = gen_zipf(N)
        dt, rate = bench_throughput(name, stream)
        peak = bench_memory(stream)
        rate_str = f"{rate:>14,.0f}" if rate != float("inf") else f"{'inf':>14}"
        print(f"{name:<14} | {len(stream):>9,} | {dt:>7.3f}s | {rate_str} | {peak/1024:>8.1f}KB")
    print("\n注：内存峰值与 N 无关，仅随容量 m 增长（固定内存预算）；"
          "空流耗时为循环本身开销。")


if __name__ == "__main__":
    main()
