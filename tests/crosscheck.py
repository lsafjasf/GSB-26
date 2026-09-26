"""Dinic 与暴力实现的随机对拍。

对每一个随机网络同时校验：
1. Dinic 最大流值 == Ford-Fulkerson 最大流值；
2. Dinic 给出的割容量 == 最大流值（流割恒等）；
3. n 很小时，割容量还等于“枚举全部源汇分离割”的最小值；
4. 每条边流量满足容量约束与所有中间点的流守恒。

用法:
    python3 tests/crosscheck.py            # 默认 2000 组
    python3 tests/crosscheck.py 5000 123   # 组数 + 随机种子
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maxflow.dinic import MaxFlow
from maxflow.brute import brute_max_flow, brute_min_cut_capacity


def random_network(rng):
    """构造带各种退化元素的随机小网络。"""
    n = rng.randint(2, 8)
    m = rng.randint(0, n * (n - 1))
    edges = []
    for _ in range(m):
        u = rng.randrange(n)
        v = rng.randrange(n)
        kind = rng.random()
        if kind < 0.25:
            capacity = 0                                   # 零容量边
        elif kind < 0.30:
            capacity = rng.choice([10**30, 10**100])       # 超大容量
        else:
            capacity = rng.randint(0, 15)
        edges.append((u, v, capacity))
    s, t = rng.sample(range(n), 2)
    return n, edges, s, t


def check_flow_conservation(mf, edges, s, t):
    n = mf.n
    balance = [0] * n
    for eid, (u, v, _) in enumerate(edges):
        f = mf.flow_on(eid)
        assert f >= 0, "出现负流量"
        assert f <= mf._edges[eid].capacity, "边流量超过容量"
        balance[u] -= f
        balance[v] += f
    for v in range(n):
        if v != s and v != t:
            assert balance[v] == 0, f"中间点 {v} 流不守恒: {balance[v]}"
    assert balance[s] == -mf._flow and balance[t] == mf._flow, "源汇净流量与流值不符"


def run_one(n, edges, s, t, *, exhaustive=True):
    mf = MaxFlow(n)
    for edge in edges:
        mf.add_edge(*edge)
    value = mf.max_flow(s, t)
    cut = mf.min_cut()
    cut_cap = mf.cut_capacity(cut)

    ref = brute_max_flow(n, edges, s, t)
    assert value == ref, (
        f"流值不一致: Dinic={value} brute={ref}\n n={n} s={s} t={t} edges={edges}")
    assert cut_cap == value, (
        f"流割不恒等: flow={value} cut_cap={cut_cap}\n edges={edges}")

    seen = set()
    for e in cut:
        assert e.id not in seen, "割中出现重复边"
        seen.add(e.id)
        assert mf.source_set()[e.u] and not mf.source_set()[e.v], "割边方向错误"

    check_flow_conservation(mf, edges, s, t)

    if exhaustive and n <= 12:
        best = brute_min_cut_capacity(n, edges, s, t)
        assert cut_cap == best, (
            f"割容量不是最小: got={cut_cap} best={best}\n n={n} edges={edges}")
    return value


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260926
    rng = random.Random(seed)

    max_value_seen = 0
    for i in range(1, trials + 1):
        n, edges, s, t = random_network(rng)
        value = run_one(n, edges, s, t)
        max_value_seen = max(max_value_seen, value)
        if i % 500 == 0:
            print(f"  已通过 {i}/{trials} 组 ...")

    print(f"OK: {trials} 组随机对拍全部通过（种子 {seed}，最大流值峰值 {max_value_seen}）")


if __name__ == "__main__":
    main()
