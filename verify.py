"""可复跑验证：遍历顺序规则 vs 实际输出逐条核对 + 可复现性 + 性能/内存。

验证内容：
  1. 小图用例：硬编码期望序列，逐条核对节点序与边序（含排序规则、
     自环、并行边、无向反向副本去重）。
  2. 独立参考实现 reference_dfs（边栈式，与 DFSWalker 的迭代器栈
     是两种不同写法）：所有规模下逐条比对节点序、边序完全一致。
  3. 可复现性：同图同配置重复遍历，序列 SHA-256 完全一致；
     大图的哈希打印出来，跨进程复跑可直接比对。
  4. 规模与资源：十万节点深链、百万节点/千万边，输出耗时与 RSS。

用法：
  python3 verify.py            # 全部用例（含 1M 节点 / 10M 边大档）
  python3 verify.py --quick    # 跳过 1M/10M 大档
退出码：全部通过为 0，任一不符为 1。
"""

import argparse
import gc
import hashlib
import sys
import time

from dfs import DFSWalker, Graph
from benchmark import build as build_big, rss_mb


def reference_dfs(graph, start):
    """独立参考实现：边栈式 DFS（与 DFSWalker 的迭代器栈不同写法）。

    遵循同一顺序规则：邻接边按 (终点, edge_id) 升序（Graph.edges_from
    已保证），先深入再回溯；无向边按 edge_id 去重。
    """
    visited = {start}
    seen_edges = set()
    node_order = [start]
    edge_order = []
    stack = list(reversed(graph.edges_from(start)))  # 逆序压栈 => 弹出即升序
    while stack:
        edge = stack.pop()
        if edge.id in seen_edges:
            continue
        seen_edges.add(edge.id)
        edge_order.append(edge.id)
        if edge.v not in visited:
            visited.add(edge.v)
            node_order.append(edge.v)
            stack.extend(reversed(graph.edges_from(edge.v)))
    return node_order, edge_order


def order_hash(seq):
    """序列的确定性指纹：跨进程、跨机器可比对。"""
    h = hashlib.sha256()
    for item in seq:
        h.update(repr(item).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


FAILURES = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"  {detail}"
    print(line, flush=True)
    if not ok:
        FAILURES.append(name)


def first_mismatch(actual, expected):
    for i, (a, e) in enumerate(zip(actual, expected)):
        if a != e:
            return f"第 {i} 项: 实际={a!r} 期望={e!r}"
    if len(actual) != len(expected):
        return f"长度不符: 实际={len(actual)} 期望={len(expected)}"
    return ""


def verify_case(name, graph, start, expected_nodes=None, expected_edges=None):
    """逐条核对：DFSWalker vs 硬编码期望 vs 独立参考实现；连跑 3 次验可复现。"""
    runs = []
    for _ in range(3):
        result = DFSWalker(graph).walk(start)
        assert result.completed
        runs.append(([e.id for e in result.edges], list(result.nodes)))
    edge_seq, node_seq = runs[0]

    check(f"{name}: 同图同配置 3 次遍历完全一致",
          all(r == runs[0] for r in runs))

    if expected_nodes is not None:
        ok = node_seq == list(expected_nodes)
        check(f"{name}: 节点序 == 规则推导的期望序列（逐条）", ok,
              "" if ok else first_mismatch(node_seq, list(expected_nodes)))
    if expected_edges is not None:
        ok = edge_seq == list(expected_edges)
        check(f"{name}: 边序 == 规则推导的期望序列（逐条）", ok,
              "" if ok else first_mismatch(edge_seq, list(expected_edges)))

    ref_nodes, ref_edges = reference_dfs(graph, start)
    check(f"{name}: 节点序 == 独立参考实现（逐条，{len(node_seq)} 项）",
          node_seq == ref_nodes,
          "" if node_seq == ref_nodes else first_mismatch(node_seq, ref_nodes))
    check(f"{name}: 边序 == 独立参考实现（逐条，{len(edge_seq)} 项）",
          edge_seq == ref_edges,
          "" if edge_seq == ref_edges else first_mismatch(edge_seq, ref_edges))
    return node_seq, edge_seq


def case_small_sorted():
    g = Graph(directed=True)
    g.add_edge("e9", 0, 5)
    g.add_edge("e1", 0, 2)
    g.add_edge("e5", 0, 3)
    g.add_edge("e0", 2, 5)
    g.add_edge("e7", 3, 5)
    verify_case("小图-排序规则", g, 0,
                expected_nodes=[0, 2, 5, 3],
                expected_edges=["e1", "e0", "e5", "e7", "e9"])


def case_small_dedup():
    g = Graph(directed=False)
    g.add_edge("s1", 1, 1)   # 自环：只存一份、处理一次
    g.add_edge("p1", 1, 2)   # 并行边 p1/p2：各处理一次
    g.add_edge("p2", 1, 2)
    g.add_edge("b1", 2, 3)   # 无向边：两端各存一份，按 edge_id 去重
    verify_case("小图-去重策略", g, 1,
                expected_nodes=[1, 2, 3],
                expected_edges=["s1", "p1", "p2", "b1"])


def case_chain(n=100_000):
    g = Graph(directed=True)
    g.add_node(0)
    for i in range(n - 1):
        g.add_edge(i, i, i + 1)
    gc.collect()
    rss0 = rss_mb()
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(200)  # 压低递归上限，证明不依赖调用栈
    try:
        t0 = time.perf_counter()
        result = DFSWalker(g).walk(0)
        walk_s = time.perf_counter() - t0
    finally:
        sys.setrecursionlimit(old_limit)
    node_seq, edge_seq = result.nodes, [e.id for e in result.edges]
    check(f"深链-{n}: 节点序 == 0..{n - 1}（逐条）",
          node_seq == list(range(n)))
    check(f"深链-{n}: 边序 == 0..{n - 2}（逐条）",
          edge_seq == list(range(n - 1)))
    ref_nodes, ref_edges = reference_dfs(g, 0)
    check(f"深链-{n}: 节点序/边序 == 独立参考实现",
          node_seq == ref_nodes and edge_seq == ref_edges)
    print(f"       耗时 {walk_s:.3f}s | 遍历额外 RSS "
          f"{rss_mb() - rss0:.1f} MB | 节点哈希 {order_hash(node_seq)[:16]}…",
          flush=True)


def case_big(n_nodes, n_edges, seed):
    print(f"--- 大档: {n_nodes:,} 节点 / {n_edges:,} 边 (seed={seed}) ---",
          flush=True)
    gc.collect()
    rss0 = rss_mb()
    t0 = time.perf_counter()
    g = build_big(n_nodes, n_edges, seed)
    gc.collect()
    build_s = time.perf_counter() - t0
    rss_graph = rss_mb() - rss0

    t0 = time.perf_counter()
    result = DFSWalker(g).walk(0)
    walk_s = time.perf_counter() - t0
    node_seq, edge_seq = result.nodes, [e.id for e in result.edges]
    rss_walk = rss_mb() - rss0 - rss_graph

    check(f"大档: 访问节点数 == {n_nodes:,}", len(node_seq) == n_nodes)
    check(f"大档: 处理边数 == {n_edges:,}", len(edge_seq) == n_edges)
    check("大档: 节点无重复", len(set(node_seq)) == len(node_seq))
    check("大档: 边无重复", len(set(edge_seq)) == len(edge_seq))

    node_hash, edge_hash = order_hash(node_seq), order_hash(edge_seq)
    result2 = DFSWalker(g).walk(0)
    check("大档: 同图同配置二次遍历哈希一致（可复现）",
          order_hash(result2.nodes) == node_hash
          and order_hash([e.id for e in result2.edges]) == edge_hash)

    t0 = time.perf_counter()
    ref_nodes, ref_edges = reference_dfs(g, 0)
    ref_s = time.perf_counter() - t0
    check(f"大档: 节点序 == 独立参考实现（逐条，{len(node_seq):,} 项）",
          node_seq == ref_nodes)
    check(f"大档: 边序 == 独立参考实现（逐条，{len(edge_seq):,} 项）",
          edge_seq == ref_edges)

    print(f"       建图 {build_s:.2f}s | 遍历 {walk_s:.2f}s"
          f"（参考实现 {ref_s:.2f}s）| 吞吐 {n_edges / walk_s / 1e6:.2f}M 边/s",
          flush=True)
    print(f"       图占用 RSS {rss_graph:.0f} MB | 遍历额外 RSS "
          f"{rss_walk:.0f} MB", flush=True)
    print(f"       节点序 SHA-256: {node_hash}", flush=True)
    print(f"       边序   SHA-256: {edge_hash}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过 1M/10M 大档")
    ap.add_argument("--nodes", type=int, default=1_000_000)
    ap.add_argument("--edges", type=int, default=10_000_000)
    ap.add_argument("--seed", type=int, default=20260926)
    args = ap.parse_args()

    case_small_sorted()
    case_small_dedup()
    case_chain()
    if not args.quick:
        case_big(args.nodes, args.edges, args.seed)

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 项未通过: {FAILURES}")
        return 1
    print("全部校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
