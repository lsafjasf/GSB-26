"""百万点降采样性能测试。运行: python3 bench.py"""
import random
import time
from downsample import Avg, Count, Max, Quantile, Sum, downsample

N = 1_000_000
WINDOW = 60  # 60 个时间单位一个窗口
SPAN = 30 * 24 * 3600  # 30 天 -> 43200 个窗口


def gen_points(n, seed=2026):
    rng = random.Random(seed)
    # 乱序 + 含重复时间戳，模拟真实到达
    return [(rng.randrange(SPAN), rng.uniform(0, 1000)) for _ in range(n)]


def bench(name, agg, points, repeat=3):
    best = float("inf")
    out = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = downsample(points, WINDOW, agg)
        best = min(best, time.perf_counter() - t0)
    filled = sum(1 for b in out if b.value is not None)
    print(
        f"{name:<14} 窗口数={len(out):>6} 非空窗口={filled:>6} "
        f"最优耗时={best*1000:8.1f} ms  ({N/best/1e6:.2f} M点/s)"
    )


def main():
    print(f"数据量={N} 点, 窗口={WINDOW}, 时间跨度={SPAN} ({SPAN//WINDOW} 个窗口)")
    points = gen_points(N)
    t0 = time.perf_counter()
    bench("sum", Sum(), points)
    bench("count", Count(), points)
    bench("max", Max(), points)
    bench("avg", Avg(), points)
    bench("quantile(0.5)", Quantile(0.5), points)
    bench("quantile(0.99)", Quantile(0.99), points)
    print(f"总耗时(含生成数据)={time.perf_counter()-t0:.2f} s")


if __name__ == "__main__":
    main()
