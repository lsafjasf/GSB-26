"""节点序 / 边序的独立校验（与 dfs.py 的实现相互独立，仅标准库）。

三层校验，逐条可核对：

1. reference_walk：用“显式 (节点, 邻接下标) 栈”重写的先序 DFS（与被校验
   实现的迭代器写法不同），独立产出节点序、边序作为参照真值。
2. 规则级断言：
   - 邻接表确已按 (目标节点键, 边 id 键) 升序（SORTED 策略）；
   - 节点序无重复、为合法先序 DFS；边序无重复、每条逻辑边恰好 1 次；
   - simple 图中不存在重复节点对；自环只出现 1 份；无向镜像被去重。
3. 可复现：节点序与边序分别做规范化 SHA-256（流式，不占大内存），
   两次独立运行（含打乱录入顺序）哈希必须完全相同。

规模用法见 verify_scale；小规模逐条核对见 verify_small（main 默认跑小图）。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional, Tuple

from dfs import (
    DFSWalker,
    Edge,
    Graph,
    OrderPolicy,
    default_order_key,
)


# ---------------------------------------------------------------------------
# 独立参照实现：显式 (节点, 下标) 栈的先序 DFS
# ---------------------------------------------------------------------------
def reference_walk(graph: Graph, start: Any = None) -> Tuple[List[Any], List[Any]]:
    """返回 (节点序, 边 id 序)。逻辑独立于 DFSWalker.walk。"""
    graph.freeze()
    adj = graph._frozen_adj
    nodes = graph._frozen_node_order
    seen_nodes: set = set()
    seen_edges: set = set()
    node_seq: List[Any] = []
    edge_seq: List[Any] = []
    roots = (start,) if start is not None else nodes
    for root in roots:
        if root in seen_nodes or root not in adj:
            continue
        seen_nodes.add(root)
        node_seq.append(root)
        stack: List[Tuple[Any, int]] = [(root, 0)]
        while stack:
            u, idx = stack[-1]
            nbrs = adj[u]
            if idx >= len(nbrs):
                stack.pop()
                continue
            edge = nbrs[idx]
            stack[-1] = (u, idx + 1)
            if not graph.directed:
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)
            edge_seq.append(edge.id)
            v = edge.v
            if v not in seen_nodes:
                seen_nodes.add(v)
                node_seq.append(v)
                stack.append((v, 0))
    return node_seq, edge_seq


# ---------------------------------------------------------------------------
# 规范化哈希（流式，供百万/千万规模使用）
# ---------------------------------------------------------------------------
def _enc(value: Any) -> bytes:
    return repr(value).encode("utf-8")


def sequence_hashes(nodes: List[Any], edges: List[Edge]) -> Dict[str, str]:
    hn = hashlib.sha256()
    for n in nodes:
        hn.update(_enc(n))
        hn.update(b"\n")
    he = hashlib.sha256()
    for e in edges:
        he.update(_enc(e.id))
        he.update(b"\n")
    return {"node_order_sha256": hn.hexdigest(), "edge_order_sha256": he.hexdigest()}


def streaming_walk_hashes(graph: Graph, start: Any = None) -> Tuple[str, str, int, int]:
    """边遍历边哈希，不保存节点/边序列；返回 (节点哈希, 边哈希, 节点数, 边数)。"""
    hn = hashlib.sha256()
    he = hashlib.sha256()
    nn = ne = 0

    def on_node(n: Any) -> None:
        nonlocal nn
        nn += 1
        hn.update(_enc(n))
        hn.update(b"\n")

    def on_edge(e: Edge) -> None:
        nonlocal ne
        ne += 1
        he.update(_enc(e.id))
        he.update(b"\n")

    DFSWalker(graph, on_node=on_node, on_edge=on_edge).walk(start)
    return hn.hexdigest(), he.hexdigest(), nn, ne


# ---------------------------------------------------------------------------
# 规则级校验
# ---------------------------------------------------------------------------
def check_adjacency_sorted(graph: Graph) -> Tuple[bool, List[str]]:
    graph.freeze()
    if graph.order is not OrderPolicy.SORTED:
        return True, ["order=INSERTION，跳过排序检查"]
    problems: List[str] = []
    for u, edges in graph._frozen_adj.items():
        keys = [
            (_KeyWrap(default_order_key(e.v)), _KeyWrap(default_order_key(e.id)))
            for e in edges
        ]
        for i in range(1, len(keys)):
            if keys[i] < keys[i - 1]:
                problems.append(f"节点 {u!r} 的邻接边在位置 {i} 未按规则排序")
                break
    return (not problems), problems or ["全部邻接表均满足 (目标键, 边id键) 升序"]


class _KeyWrap:
    __slots__ = ("v",)

    def __init__(self, v: Any) -> None:
        self.v = v

    def __lt__(self, o: "_KeyWrap") -> bool:
        try:
            return self.v < o.v
        except TypeError:
            return repr(self.v) < repr(o.v)


def check_invariants(
    graph: Graph,
    node_seq: List[Any],
    edge_ids: List[Any],
    reachable_nodes: Optional[set] = None,
) -> Tuple[bool, List[str]]:
    msgs: List[str] = []
    ok = True
    # 节点：无重复
    if len(node_seq) == len(set(node_seq)):
        msgs.append(f"节点序无重复（{len(node_seq)} 个）")
    else:
        ok = False
        msgs.append("节点序存在重复！")
    # 边：无重复
    if len(edge_ids) == len(set(edge_ids)):
        msgs.append(f"边序无重复（{len(edge_ids)} 条）")
    else:
        ok = False
        msgs.append("边序存在重复！")

    # 每条“可达”逻辑边恰好 1 次：与图的结构可达边集合对齐。
    # 单源遍历时，仅源可达分量内的边应被遍历（reachable_nodes 给定）。
    graph.freeze()
    reach = reachable_nodes if reachable_nodes is not None else set(node_seq)
    logical_ids: set = set()
    for edges in graph._frozen_adj.values():
        for e in edges:
            if graph.directed:
                in_scope = e.u in reach
            else:  # 无向：边的任一端可达即会被遍历
                in_scope = e.u in reach or e.v in reach
            if in_scope:
                logical_ids.add(e.id)
    expect_edges = len(logical_ids)
    if set(edge_ids) == logical_ids and len(edge_ids) == expect_edges:
        msgs.append(
            f"每条可达逻辑边恰好触发 1 次（共 {expect_edges} 条，与结构一致）")
    else:
        ok = False
        msgs.append(
            f"边集合不一致：遍历 {len(set(edge_ids))} / 可达结构 {expect_edges}")

    # simple：无重复节点对（无向镜像按 edge_id 先去重，再规范化节点对）
    if graph.duplicate_policy == "simple":
        seen_pairs: set = set()
        dup = False
        checked_ids: set = set()
        for edges in graph._frozen_adj.values():
            for e in edges:
                if e.id in checked_ids:
                    continue
                checked_ids.add(e.id)
                if graph.directed:
                    pair = (e.u, e.v)
                else:
                    pair = tuple(sorted((e.u, e.v), key=_sort_key))
                if pair in seen_pairs:
                    dup = True
                seen_pairs.add(pair)
        if dup:
            ok = False
            msgs.append("simple 图仍存在重复节点对！")
        else:
            msgs.append(f"simple 图：无重复节点对（{len(seen_pairs)} 对，折叠生效）")

    # 自环只存 1 份
    for u, edges in graph._frozen_adj.items():
        loops = [e for e in edges if e.u == e.v]
        ids = [e.id for e in loops]
        if len(ids) != len(set(ids)):
            ok = False
            msgs.append(f"自环在 {u!r} 邻接表重复！")
    msgs.append("自环在邻接表中至多 1 份")
    return ok, msgs


def _sort_key(x: Any) -> Any:
    return _KeyWrap(default_order_key(x))


def check_against_reference(
    graph: Graph, start: Any = None
) -> Tuple[bool, List[str], Dict[str, str]]:
    """被校验实现 vs 独立参照实现：节点序、边序逐元素相等，并给哈希。"""
    ref_nodes, ref_edges = reference_walk(graph, start)
    res = DFSWalker(graph).walk(start)
    got_nodes = res.nodes
    got_edges = [e.id for e in res.edges]
    msgs: List[str] = []
    ok = True
    if got_nodes == ref_nodes:
        msgs.append(f"节点序与独立参照逐元素一致（{len(got_nodes)} 个）")
    else:
        ok = False
        msgs.append("节点序与参照不一致："
                    + _first_diff(got_nodes, ref_nodes))
    if got_edges == ref_edges:
        msgs.append(f"边序与独立参照逐元素一致（{len(got_edges)} 条）")
    else:
        ok = False
        msgs.append("边序与参照不一致：" + _first_diff(got_edges, ref_edges))
    hashes = sequence_hashes(got_nodes, res.edges)
    inv_ok, inv_msgs = check_invariants(
        graph, got_nodes, got_edges, reachable_nodes=set(ref_nodes))
    sort_ok, sort_msgs = check_adjacency_sorted(graph)
    ok = ok and inv_ok and sort_ok
    msgs += inv_msgs + sort_msgs
    return ok, msgs, hashes


def _first_diff(a: List[Any], b: List[Any]) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"首个差异在第 {i} 项：got={x!r} ref={y!r}"
    return f"长度不同：got={len(a)} ref={len(b)}"


# ---------------------------------------------------------------------------
# 小规模：覆盖自环/并行边/无向/菱形/simple 折叠，打印可逐条核对的序列
# ---------------------------------------------------------------------------
def build_showcase(
    directed: bool, policy: str, order: OrderPolicy = OrderPolicy.SORTED
) -> Graph:
    g = Graph(directed=directed, order=order, duplicate_policy=policy)
    # 故意“乱序”录入，证明输出只取决于图与配置、与录入顺序无关
    g.add_edge("e04", 0, 4)
    g.add_edge("e02", 0, 2)
    g.add_edge("e32", 3, 2)
    g.add_edge("lp", 0, 0)    # 自环
    g.add_edge("p_b", 0, 1)   # 并行边（与 p_a 同端点、不同 id）
    g.add_edge("p_a", 0, 1)
    g.add_edge("e01", 0, 1)
    g.add_edge("e13", 1, 3)
    return g


def verify_small() -> bool:
    all_ok = True
    for directed in (False, True):
        for policy in ("multigraph", "simple"):
            g = build_showcase(directed, policy)
            tag = f"{'有向' if directed else '无向'}-{policy}"
            ok, msgs, hashes = check_against_reference(g, start=0)
            res = DFSWalker(g).walk(0)
            print(f"== {tag} ==")
            print("  邻接表:")
            for n in g.ordered_nodes():
                print(f"    {n!r}: {[(e.id, e.v) for e in g.edges_from(n)]}")
            print(f"  节点序: {res.nodes}")
            print(f"  边  序: {[e.id for e in res.edges]}")
            for m in msgs:
                print(f"    - {m}")
            print(f"    node_sha={hashes['node_order_sha256'][:16]} "
                  f"edge_sha={hashes['edge_order_sha256'][:16]}")
            print(f"  => {'PASS' if ok else 'FAIL'}\n")
            all_ok = all_ok and ok
    return all_ok


# ---------------------------------------------------------------------------
# 可复现：同一图配置，打乱录入顺序后哈希必须一致
# ---------------------------------------------------------------------------
def reproducibility_check(
    make_edges, n_nodes: int, directed: bool, policy: str, seed: int
) -> Tuple[bool, dict]:
    def build(shuffle: bool) -> Graph:
        g = Graph(directed=directed, duplicate_policy=policy)
        for n in range(n_nodes):
            g.add_node(n)
        # (eid, u, v)：edge_id 是边的固有身份，必须在打乱前就绑定好，
        # 否则并行边在两次构建中会拿到不同 id（那就不是“同一图”了）。
        triples = [(eid, u, v) for eid, (u, v) in enumerate(make_edges())]
        if shuffle:
            random.Random(seed).shuffle(triples)
        for eid, u, v in triples:
            g.add_edge(eid, u, v)
        return g

    g1 = build(False)
    g2 = build(True)
    start = 0 if any(u == 0 for u, _ in make_edges()) else None
    h1 = streaming_walk_hashes(g1, start)
    h2 = streaming_walk_hashes(g2, start)
    # 同一对象再跑一次（同进程可复现）
    h3 = streaming_walk_hashes(g1, start)
    ok = h1 == h2 == h3
    detail = {
        "insertion_order_hashes": {
            "node": h1[0], "edge": h1[1], "nodes": h1[2], "edges": h1[3]},
        "shuffled_order_hashes": {
            "node": h2[0], "edge": h2[1], "nodes": h2[2], "edges": h2[3]},
        "identical": ok,
    }
    return ok, detail


if __name__ == "__main__":
    ok = verify_small()
    raise SystemExit(0 if ok else 1)
