"""百万点降采样性能基准。"""

import random
import time

from downsample import downsample, naive_downsample

N = 1_000_000
WINDOW = 60


def gen_points(n, seed=0):
    rng = random.Random(seed)
    # 1 秒一个点，跨度约 11.6 天，含乱序与重复时间戳
    pts = [(rng.randrange(0, n), rng.uniform(0, 1000)) for _ in range(n)]
    return pts


def run(label, fn, pts, window, aggs, **kw):
    t0 = time.perf_counter()
    out = fn(pts, window, aggs, **kw)
    dt = time.perf_counter() - t0
    nonempty = sum(1 for b in out if not b.empty)
    print(f"{label:<28} aggs={','.join(aggs):<18} "
          f"windows={len(out):>6} (非空 {nonempty:>6})  耗时 {dt*1000:8.1f} ms")
    return dt


def main():
    print(f"数据量: {N:,} 点, 窗口: {WINDOW}s, Python 标准库实现")
    pts = gen_points(N)

    run("downsample(可加)", downsample, pts, WINDOW, ["count", "sum"])
    run("downsample(不可加)", downsample, pts, WINDOW, ["max", "avg", "p50", "p95"])

    # 小样本上的朴素对拍耗时（百万点朴素为 O(n*windows)，仅演示量级）
    small = pts[:20_000]
    run("naive_downsample(2万点)", naive_downsample, small, WINDOW, ["sum"])

    # 乱序无关性在百万点上再确认一次
    shuffled = pts[:]
    random.Random(1).shuffle(shuffled)
    a = downsample(pts, WINDOW, ["count", "sum"])
    b = downsample(shuffled, WINDOW, ["count", "sum"])
    assert a == b, "乱序结果不一致!"
    print("百万点乱序一致性: OK")


if __name__ == "__main__":
    main()
