"""stress_test.py — Dinic 与暴力增广在随机小图上对拍。

校验项：
1. 两者最大流值一致；
2. 两者最小割容量一致，且各自等于流值（对偶性）；
3. Dinic 给出的割边集合合法（全部 S->T，容量和 = 流值）；
4. Dinic 的流满足容量约束与流量守恒。

用法: python3 stress_test.py [轮数] [随机种子]
"""

import random
import sys

from maxflow import MaxFlow
from bruteforce import BruteForceMaxFlow


def random_graph(rng):
    n = rng.randint(2, 8)
    m = rng.randint(0, 3 * n)
    edges = []
    for _ in range(m):
        u = rng.randrange(n)
        v = rng.randrange(n)          # 允许自环
        # 容量分布：大量 0、小整数、偶发大数
        r = rng.random()
        if r < 0.15:
            cap = 0
        elif r < 0.9:
            cap = rng.randint(1, 20)
        else:
            cap = rng.randint(1, 10**12)
        edges.append((u, v, cap))
    s = rng.randrange(n)
    t = rng.randrange(n)
    return n, edges, s, t


def check_flow_valid(mf, s, t, value):
    """容量约束 + 流量守恒。"""
    n = mf.n
    net = [0] * n
    for (u, v, cap, fwd) in mf._orig:
        f = cap - fwd.cap
        assert 0 <= f <= cap, f"capacity violated on {(u, v, cap)}: flow {f}"
        net[u] -= f
        net[v] += f
    for x in range(n):
        if x == s:
            assert net[x] == -value, "source imbalance"
        elif x == t:
            assert net[x] == value, "sink imbalance"
        else:
            assert net[x] == 0, f"conservation violated at {x}"


def run_rounds(rounds, seed):
    rng = random.Random(seed)
    for it in range(1, rounds + 1):
        n, edges, s, t = random_graph(rng)

        a = MaxFlow(n)
        b = BruteForceMaxFlow(n)
        for u, v, c in edges:
            a.add_edge(u, v, c)
            b.add_edge(u, v, c)

        fa = a.max_flow(s, t)
        fb = b.max_flow(s, t)
        assert fa == fb, f"round {it}: flow mismatch dinic={fa} brute={fb}\n" \
                         f"n={n} s={s} t={t} edges={edges}"

        ca, cut_edges, S = a.min_cut(s, t)
        cb, _, _ = b.min_cut(s, t)
        assert ca == fa, f"round {it}: dinic cut {ca} != flow {fa}"
        assert cb == fb, f"round {it}: brute cut {cb} != flow {fb}"
        assert ca == cb, f"round {it}: cut mismatch {ca} vs {cb}"

        if s != t:
            assert S[s] and not S[t], f"round {it}: invalid side assignment"
            for (u, v, c) in cut_edges:
                assert S[u] and not S[v], f"round {it}: cut edge crosses wrong way"
            assert sum(c for _, _, c in cut_edges) == fa
            check_flow_valid(a, s, t, fa)

        if it % 2000 == 0:
            print(f"  ... {it} rounds OK", flush=True)
    print(f"PASS: {rounds} rounds, seed={seed}")


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
    run_rounds(rounds, seed)
