"""大规模 DFS 的耗时 / 内存 / 顺序校验基准（仅标准库）。

三档场景，结果可直接复跑核对：
  chain   100,000 节点深链（验证不依赖递归深度 + 顺序严格递增）
  medium  100,000 节点 / 1,000,000 边（独立参照逐元素交叉校验 + 打乱录入可复现）
  large   1,000,000 节点 / 10,000,000 边（流式哈希校验 + 打乱录入可复现）

用法：
  python3 benchmark.py                 # 三档全跑
  python3 benchmark.py --only large
  python3 benchmark.py --nodes 1000000 --edges 10000000 --seed 20260928

指标：建图 / freeze 排序 / 遍历耗时（预热 2 轮后 3 轮中位数）、图占用 RSS、
遍历额外峰值 RSS、访问节点 / 处理边计数、节点序与边序 SHA-256、
打乱录入顺序后的哈希一致性、与独立参照实现的逐元素比对（medium 档）。
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import statistics
import threading
import time
from typing import Callable, List, Tuple

from dfs import DFSWalker, Graph
from verify import (
    reference_walk,
    streaming_walk_hashes,
)

SEED = 20260928


# ---------------------------------------------------------------------------
# 内存采样
# ---------------------------------------------------------------------------
def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * 4096 / 1e6


class RssSampler(threading.Thread):
    """高频读取 RSS，捕捉遍历过程中的峰值（遍历结束即清理，事后测不到）。"""

    def __init__(self, interval: float = 0.01) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self._stop_evt = threading.Event()
        self.peak = rss_mb()
        self.ready = threading.Event()

    def run(self) -> None:
        self.ready.set()
        while not self._stop_evt.is_set():
            value = rss_mb()
            if value > self.peak:
                self.peak = value
            self._stop_evt.wait(self.interval)

    def stop(self) -> float:
        self._stop_evt.set()
        self.join(timeout=1.0)
        return self.peak


# ---------------------------------------------------------------------------
# 确定性图生成：返回 (图, 生成 triples 的函数) 以便“打乱录入”重建
# ---------------------------------------------------------------------------
def lcg(seed: int):
    state = seed & 0xFFFFFFFF
    while True:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        yield state


def chain_triples(n: int) -> List[Tuple[int, int, int]]:
    return [(i, i, i + 1) for i in range(n - 1)]


def random_triples(n_nodes: int, n_edges: int, seed: int) -> List[Tuple[int, int, int]]:
    rand = lcg(seed)
    triples: List[Tuple[int, int, int]] = []
    for eid in range(n_edges):
        if eid < n_nodes - 1:
            u, v = eid, eid + 1          # 主干保证从 0 全可达
        else:
            u, v = next(rand) % n_nodes, next(rand) % n_nodes
        triples.append((eid, u, v))
    return triples


def build_directed(n_nodes: int, triples: List[Tuple[int, int, int]],
                   shuffle: bool = False, seed: int = SEED) -> Tuple[Graph, float, float]:
    data = triples[:]
    if shuffle:
        random.Random(seed + 1).shuffle(data)
    g = Graph(directed=True)
    t0 = time.perf_counter()
    for _ in range(n_nodes):
        g.add_node(len(g._adj))
    for eid, u, v in data:
        g.add_edge(eid, u, v)
    t1 = time.perf_counter()
    g.freeze()
    t2 = time.perf_counter()
    return g, t1 - t0, t2 - t1


# ---------------------------------------------------------------------------
# 计时与内存
# ---------------------------------------------------------------------------
def timed_walk(g: Graph, rounds: int = 3, warmup: int = 2) -> dict:
    node_count = edge_count = 0

    def on_node(_n):
        nonlocal node_count
        node_count += 1

    def on_edge(_e):
        nonlocal edge_count
        edge_count += 1

    walker = DFSWalker(g, on_node=on_node, on_edge=on_edge)
    for _ in range(warmup):
        r = walker.walk(0, track_nodes=False, track_edges=False)
        assert r.completed
    gc.collect()
    rss_before = rss_mb()
    times, peak = [], rss_before
    for _ in range(rounds):
        node_count = edge_count = 0
        sampler = RssSampler()
        sampler.start()
        sampler.ready.wait()
        ta = time.perf_counter()
        result = walker.walk(0, track_nodes=False, track_edges=False)
        tb = time.perf_counter()
        peak = max(peak, sampler.stop())
        times.append(tb - ta)
    gc.collect()
    rss_after = rss_mb()
    med = statistics.median(times)
    return {
        "walk_seconds_median": round(med, 3),
        "walk_seconds_runs": [round(x, 3) for x in times],
        "edges_per_second": int(g.num_edges / med),
        "visited_nodes": node_count,
        "processed_edges": edge_count,
        "completed": result.completed,
        "walk_extra_peak_rss_mb": round(peak - rss_before, 1),
        "walk_residual_rss_mb": round(rss_after - rss_before, 1),
    }


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------
def run_chain(n: int = 100_000) -> dict:
    gc.collect()
    rss0 = rss_mb()
    triples = chain_triples(n)
    g, build_s, freeze_s = build_directed(n, triples)
    gc.collect()
    rss_graph = rss_mb() - rss0
    walk = timed_walk(g)
    node_hash, edge_hash, nn, ne = streaming_walk_hashes(g, 0)
    # 顺序硬校验：深链必须严格 0..n-1、边 0..n-2
    strict_ok = (node_hash == streaming_walk_hashes(g, 0)[0])
    return {
        "scenario": f"chain {n:,} nodes",
        "nodes": g.num_nodes, "edges": g.num_edges,
        "build_seconds": round(build_s, 3),
        "freeze_seconds": round(freeze_s, 3),
        "graph_rss_mb": round(rss_graph, 1),
        **walk,
        "node_order_sha256": node_hash,
        "edge_order_sha256": edge_hash,
        "order_strictly_increasing": _chain_is_strict(g, n),
        "repeat_hash_identical": strict_ok,
    }


def _chain_is_strict(g: Graph, n: int) -> bool:
    prev = -1
    ok_nodes = True

    def cn(x):
        nonlocal prev, ok_nodes
        ok_nodes = ok_nodes and x == prev + 1
        prev = x

    DFSWalker(g, on_node=cn).walk(0, track_nodes=False, track_edges=False)
    return ok_nodes


def run_random(n_nodes: int, n_edges: int, seed: int,
               cross_check: bool) -> dict:
    gc.collect()
    rss0 = rss_mb()
    triples = random_triples(n_nodes, n_edges, seed)
    g, build_s, freeze_s = build_directed(n_nodes, triples, shuffle=False)
    gc.collect()
    rss_graph = rss_mb() - rss0
    walk = timed_walk(g)
    h_node, h_edge, nn, ne = streaming_walk_hashes(g, 0)

    # 打乱录入顺序重建：SORTED 下游历哈希必须完全一致
    g2, b2, f2 = build_directed(n_nodes, triples, shuffle=True, seed=seed)
    h2_node, h2_edge, nn2, ne2 = streaming_walk_hashes(g2, 0)
    repro = {
        "node_hash_identical": h_node == h2_node,
        "edge_hash_identical": h_edge == h2_edge,
        "shuffled_build_seconds": round(b2, 3),
        "shuffled_freeze_seconds": round(f2, 3),
    }

    out = {
        "scenario": f"random {n_nodes:,} nodes / {n_edges:,} edges",
        "nodes": g.num_nodes, "edges": g.num_edges, "seed": seed,
        "build_seconds": round(build_s, 3),
        "freeze_seconds": round(freeze_s, 3),
        "graph_rss_mb": round(rss_graph, 1),
        **walk,
        "node_order_sha256": h_node,
        "edge_order_sha256": h_edge,
        "reproducibility_after_shuffle": repro,
    }

    if cross_check:
        ref_nodes, ref_edges = reference_walk(g, 0)
        res = DFSWalker(g).walk(0)
        out["reference_cross_check"] = {
            "node_order_elementwise_equal": res.nodes == ref_nodes,
            "edge_order_elementwise_equal":
                [e.id for e in res.edges] == ref_edges,
            "reference_nodes": len(ref_nodes),
            "reference_edges": len(ref_edges),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["chain", "medium", "large"],
                    default=None)
    ap.add_argument("--nodes", type=int, default=1_000_000)
    ap.add_argument("--edges", type=int, default=10_000_000)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    report = {}
    if args.only in (None, "chain"):
        report["chain"] = run_chain(100_000)
    if args.only in (None, "medium"):
        report["medium"] = run_random(100_000, 1_000_000, args.seed,
                                      cross_check=True)
    if args.only in (None, "large"):
        report["large"] = run_random(args.nodes, args.edges, args.seed,
                                     cross_check=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
