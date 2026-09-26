"""benchmark.py — 稀疏 / 稠密 / 二分匹配 网络耗时测试。

用法: python3 benchmark.py
"""

import random
import time

from maxflow import MaxFlow


def bench(name, n, edges, s, t, repeat=3):
    best = None
    flow = None
    for _ in range(repeat):
        mf = MaxFlow(n)
        t0 = time.perf_counter()
        for u, v, c in edges:
            mf.add_edge(u, v, c)
        build = time.perf_counter() - t0
        t0 = time.perf_counter()
        flow = mf.max_flow(s, t)
        solve = time.perf_counter() - t0
        total = build + solve
        best = total if best is None else min(best, total)
    print(f"{name:<28} V={n:<6} E={len(edges):<8} "
          f"flow={flow:<12} build={build*1e3:8.1f}ms "
          f"solve={solve*1e3:8.1f}ms  best_total={best*1e3:8.1f}ms")


def sparse_random(n, avg_deg, seed):
    rng = random.Random(seed)
    m = n * avg_deg
    edges = []
    for _ in range(m):
        u = rng.randrange(n)
        v = rng.randrange(n)
        if u != v:
            edges.append((u, v, rng.randint(1, 10**6)))
    return edges


def dense_random(n, keep, seed):
    rng = random.Random(seed)
    edges = []
    for u in range(n):
        for v in range(n):
            if u != v and rng.random() < keep:
                edges.append((u, v, rng.randint(1, 10**6)))
    return edges


def bipartite_matching(p, q, edge_prob, seed):
    """二分匹配：源->左(1)，左->右(0/1)，右->汇(1)。Dinic 为 O(E*sqrt(V))。"""
    rng = random.Random(seed)
    n = p + q + 2
    s, t = n - 2, n - 1
    edges = []
    for i in range(p):
        edges.append((s, i, 1))
    for j in range(q):
        edges.append((p + j, t, 1))
    for i in range(p):
        for j in range(q):
            if rng.random() < edge_prob:
                edges.append((i, p + j, 1))
    return edges, s, t


def layered_hard(n_layers, width, seed):
    """分层网络（Dinic 不利情形之一）：层间全连接。"""
    rng = random.Random(seed)
    n = n_layers * width + 2
    s, t = n - 2, n - 1
    edges = []
    for i in range(width):
        edges.append((s, i, rng.randint(1, 100)))
        edges.append(((n_layers - 1) * width + i, t, rng.randint(1, 100)))
    for l in range(n_layers - 1):
        for i in range(width):
            for j in range(width):
                edges.append((l * width + i, (l + 1) * width + j,
                              rng.randint(1, 100)))
    return edges, s, t


if __name__ == "__main__":
    print("== 稀疏网络 (E ~ 4V) ==")
    for n in (10_000, 50_000, 100_000):
        bench(f"sparse n={n}", n, sparse_random(n, 4, seed=1), 0, n - 1)

    print("\n== 稠密网络 (E ~ 0.5*V^2) ==")
    for n in (300, 700, 1200):
        bench(f"dense n={n}", n, dense_random(n, 0.5, seed=2), 0, n - 1)

    print("\n== 完全稠密 (E = V*(V-1)) ==")
    for n in (200, 400):
        bench(f"complete n={n}", n, dense_random(n, 1.0, seed=3), 0, n - 1)

    print("\n== 二分匹配 (单位容量, O(E*sqrt(V))) ==")
    for p, prob in ((2000, 0.01), (5000, 0.004)):
        edges, s, t = bipartite_matching(p, p, prob, seed=4)
        bench(f"bipartite p=q={p}", 2 * p + 2, edges, s, t)

    print("\n== 分层网络 (对抗性结构) ==")
    for layers, w in ((20, 100), (10, 200)):
        edges, s, t = layered_hard(layers, w, seed=5)
        bench(f"layered L={layers} w={w}", layers * w + 2, edges, s, t)
