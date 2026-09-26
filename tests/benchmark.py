"""稠密 / 稀疏网络耗时基准（仅标准库）。

用法:
    python3 tests/benchmark.py             # 默认规模
    python3 tests/benchmark.py --quick     # 较小规模，秒级完成

每组规模重复求解 3 次（每次重新建图），报告最短与中位耗时，
并校验 流值 == 割容量。
"""

import argparse
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maxflow.dinic import MaxFlow


def sparse_layered(rng, n):
    """典型稀疏有向图：平均出度约 4，含骨架链保证 0 -> n-1 可达。

    边包含跨整个编号空间的随机边（含反向边），最短路径短、相位少，
    代表容量规划中常见的稀疏网络；另含一条 0-1-...-n-1 骨架链。
    """
    edges = []
    for u in range(n - 1):
        edges.append((u, u + 1, rng.randint(1, 1000)))  # 骨架链
        for _ in range(3):
            v = rng.randrange(n)
            if v != u:
                edges.append((u, v, rng.randint(1, 1000)))
    return edges, 0, n - 1


def dense_random(rng, n, p):
    """稠密一般有向图（含环、反平行边、重边由独立采样天然产生）。"""
    edges = []
    for u in range(n):
        for v in range(n):
            if u != v and rng.random() < p:
                edges.append((u, v, rng.randint(1, 100)))
    # 保证 s->t 至少一条路径
    edges.append((0, n - 1, 1))
    return edges, 0, n - 1


def huge_capacity_network(rng, n):
    """稀疏网络但容量在 10^2~10^800 之间，验证容量尺度不影响速度与精度。"""
    edges, s, t = sparse_layered(rng, n)
    scaled = []
    for u, v, _ in edges:
        digits = rng.randint(2, 200)
        scaled.append((u, v, rng.randint(10**digits, 10**(digits + 1) - 1)))
    return scaled, s, t


def build_and_solve(n, edges, s, t):
    t0 = time.perf_counter()
    mf = MaxFlow(n)
    for edge in edges:
        mf.add_edge(*edge)
    t1 = time.perf_counter()
    value = mf.max_flow(s, t)
    t2 = time.perf_counter()
    cut = mf.min_cut()
    cap = mf.cut_capacity(cut)
    assert cap == value, "流值与割容量不一致"
    return value, len(cut), t1 - t0, t2 - t1


def run_case(name, repeats, gen):
    rng = random.Random(42)
    n, raw_edges, s, t = gen(rng)
    # 预生成一次，取边数（每次重复使用同一网络但重新建图求解）
    build_times, solve_times = [], []
    value = cut_size = 0
    for _ in range(repeats):
        value, cut_size, bt, st = build_and_solve(n, raw_edges, s, t)
        build_times.append(bt)
        solve_times.append(st)
    digits = len(str(value))
    approx = f"≈{float(value):.3g}" if digits <= 18 else f"10^{digits} 量级"
    print(f"{name}")
    print(f"  顶点 n={n:,}  边 m={len(raw_edges):,}  重复 {repeats} 次")
    print(f"  建图: 最短 {min(build_times)*1e3:9.1f} ms  中位 {statistics.median(build_times)*1e3:9.1f} ms")
    print(f"  求解: 最短 {min(solve_times)*1e3:9.1f} ms  中位 {statistics.median(solve_times)*1e3:9.1f} ms")
    print(f"  流值为 {digits} 位十进制整数（{approx}），最小割 {cut_size:,} 条边，流值==割容量 ✓")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    if args.quick:
        run_case("稀疏分层网络", args.repeats, lambda r: (2000,) + sparse_layered(r, 2000))
        run_case("稠密随机有向图", args.repeats, lambda r: (200,) + dense_random(r, 200, 0.15))
        run_case("超大容量稀疏网络", args.repeats, lambda r: (2000,) + huge_capacity_network(r, 2000))
    else:
        run_case("稀疏分层网络", args.repeats, lambda r: (20000,) + sparse_layered(r, 20000))
        run_case("稠密随机有向图", args.repeats, lambda r: (1000,) + dense_random(r, 1000, 0.10))
        run_case("超大容量稀疏网络", args.repeats, lambda r: (5000,) + huge_capacity_network(r, 5000))


if __name__ == "__main__":
    main()
