"""百万节点 / 千万边规模的 DFS 性能与内存基准（仅标准库）。

用法：
  python3 benchmark.py                    # 默认 1_000_000 节点 / 10_000_000 边
  python3 benchmark.py --nodes 100000 --edges 1000000

指标：
  - 建图耗时、遍历耗时（perf_counter，墙钟）
  - 进程常驻内存 RSS（/proc/self/statm，页大小 4KB）
  - 遍历额外内存（遍历中 RSS 高点 - 遍历前 RSS）
  - 遍历结果：访问节点数、处理边数（断言等于全部节点/边）
图：有向多重图；主干 i -> i+1 保证从 0 全可达，其余边由确定性
LCG 伪随机生成（可复现）。
"""

import argparse
import gc
import json
import statistics
import threading
import time

from dfs import DFSWalker, Graph


def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        pages = int(f.read().split()[1])
    return pages * 4096 / 1e6


def lcg(seed: int):
    state = seed & 0xFFFFFFFF
    while True:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        yield state


def build(n_nodes: int, n_edges: int, seed: int) -> Graph:
    g = Graph(directed=True)
    for n in range(n_nodes):
        g.add_node(n)
    rand = lcg(seed)
    for eid in range(n_edges):
        if eid < n_nodes - 1:
            u, v = eid, eid + 1  # 主干
        else:
            u = next(rand) % n_nodes
            v = next(rand) % n_nodes
        g.add_edge(eid, u, v)
    return g


class RssSampler(threading.Thread):
    """高频读取 /proc RSS，捕捉遍历过程中的峰值（遍历结束即清理，事后测不到）。"""

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


def run(n_nodes: int, n_edges: int, seed: int) -> dict:
    gc.collect()
    rss0 = rss_mb()

    t0 = time.perf_counter()
    g = build(n_nodes, n_edges, seed)
    gc.collect()
    t1 = time.perf_counter()
    rss_after_build = rss_mb()

    node_count = 0
    edge_count = 0

    def count_node(_node):
        nonlocal node_count
        node_count += 1

    def count_edge(_edge):
        nonlocal edge_count
        edge_count += 1

    walker = DFSWalker(g, on_node=count_node, on_edge=count_edge)

    # 预热两轮：消除惰性缺页、分配器抖动；随后计时 3 轮取中位数。
    for _ in range(2):
        r = walker.walk(0, track_nodes=False, track_edges=False)
        assert r.completed
    gc.collect()
    rss_before_walk = rss_mb()

    walk_times = []
    peak_during_walk = rss_before_walk
    for _ in range(3):
        node_count = 0
        edge_count = 0
        sampler = RssSampler()
        sampler.start()
        sampler.ready.wait()
        ta = time.perf_counter()
        result = walker.walk(0, track_nodes=False, track_edges=False)
        tb = time.perf_counter()
        peak_during_walk = max(peak_during_walk, sampler.stop())
        walk_times.append(tb - ta)
    gc.collect()
    rss_after_walk = rss_mb()

    assert node_count == n_nodes, (node_count, n_nodes)
    assert edge_count == n_edges, (edge_count, n_edges)
    assert result.completed
    walk_median = statistics.median(walk_times)

    return {
        "nodes": n_nodes,
        "edges": n_edges,
        "seed": seed,
        "build_seconds": round(t1 - t0, 3),
        "walk_seconds_median": round(walk_median, 3),
        "walk_seconds_runs": [round(x, 3) for x in walk_times],
        "edges_per_second": int(n_edges / walk_median),
        "rss_start_mb": round(rss0, 1),
        "rss_after_build_mb": round(rss_after_build, 1),
        "graph_rss_mb": round(rss_after_build - rss0, 1),
        "rss_before_walk_mb": round(rss_before_walk, 1),
        "rss_after_walk_mb": round(rss_after_walk, 1),
        "walk_extra_rss_after_mb": round(rss_after_walk - rss_before_walk, 1),
        "walk_peak_rss_mb": round(peak_during_walk, 1),
        "walk_extra_peak_rss_mb": round(peak_during_walk - rss_before_walk, 1),
        "visited_nodes": node_count,
        "processed_edges": edge_count,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=1_000_000)
    ap.add_argument("--edges", type=int, default=10_000_000)
    ap.add_argument("--seed", type=int, default=20260926)
    args = ap.parse_args()
    stats = run(args.nodes, args.edges, args.seed)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
