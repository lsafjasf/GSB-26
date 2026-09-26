"""环检测：迭代 Tarjan SCC + 分量级 Johnson 基本环枚举。

- 每个基本环输出函数名序列（路径），可用 verify_cycle 验证相邻边真实存在。
- 自环（length 1）为直接递归；length >= 2 为相互递归。
- 环的置信度：所有边确定 -> "certain"；含不确定边 -> "potential"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .graph import CallGraph, is_internal


@dataclass
class Cycle:
    path: Tuple[str, ...]          # 规范化后的函数名序列（旋转到字典序最小者开头）
    kind: str                      # "self-recursion" | "mutual-recursion"
    confidence: str                # "certain" | "potential"
    edges: Tuple[Tuple[str, str], ...]

    @property
    def length(self) -> int:
        return len(self.path)

    @property
    def functions(self) -> Set[str]:
        return set(self.path)


def _canonical(path: List[str]) -> Tuple[str, ...]:
    i = min(range(len(path)), key=lambda k: path[k])
    return tuple(path[i:] + path[:i])


def _tarjan_scc(nodes: Iterable[str],
                succ_of) -> List[List[str]]:
    """迭代版 Tarjan，返回 SCC 列表（每个 SCC 为节点列表）。"""
    index_of: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    sccs: List[List[str]] = []
    counter = 0
    for root in nodes:
        if root in index_of:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index_of[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            recurse = False
            succs = succ_of(v)
            i = pi
            while i < len(succs):
                w = succs[i]
                if w not in index_of:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recurse = True
                    break
                elif w in on_stack:
                    low[v] = min(low[v], index_of[w])
                i += 1
            if recurse:
                continue
            if low[v] == index_of[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return sccs


def _johnson_circuits(nodes: List[str], adj: Dict[str, List[str]]) -> List[List[str]]:
    """Johnson 算法：枚举 nodes 导出子图中的所有基本环（节点数 >= 2）。"""
    order = {v: i for i, v in enumerate(nodes)}
    circuits: List[List[str]] = []
    blocked: Set[str] = set()
    B: Dict[str, Set[str]] = {v: set() for v in nodes}
    path: List[str] = []

    def least_scc(start: int) -> Optional[List[str]]:
        cand = [v for v in nodes if order[v] >= start]
        if not cand:
            return None
        sub_adj = {v: [w for w in adj[v] if order[w] >= start] for v in cand}
        best = None
        for comp in _tarjan_scc(cand, lambda v: sub_adj.get(v, [])):
            if len(comp) < 2:
                continue
            if best is None or min(order[v] for v in comp) < min(order[v] for v in best):
                best = comp
        return best

    def unblock(v: str) -> None:
        stack = [v]
        while stack:
            u = stack.pop()
            if u in blocked:
                blocked.discard(u)
                for w in B[u]:
                    stack.append(w)
                B[u].clear()

    def circuit(v: str, s: str, comp_adj: Dict[str, List[str]]) -> bool:
        found = False
        path.append(v)
        blocked.add(v)
        for w in comp_adj[v]:
            if w == s:
                circuits.append(list(path))
                found = True
            elif w not in blocked:
                if circuit(w, s, comp_adj):
                    found = True
        if found:
            unblock(v)
        else:
            for w in comp_adj[v]:
                B[w].add(v)
        path.pop()
        return found

    s_idx = 0
    n = len(nodes)
    while s_idx < n:
        comp = least_scc(s_idx)
        if comp is None:
            break
        comp_set = set(comp)
        comp_adj = {v: [w for w in adj[v] if w in comp_set] for v in comp}
        s = min(comp, key=lambda v: order[v])
        circuit(s, s, comp_adj)
        s_idx = order[s] + 1
        blocked.clear()
        for v in B:
            B[v].clear()
    return circuits


def find_cycles(graph: CallGraph, restrict_to: Optional[Set[str]] = None) -> List[Cycle]:
    """枚举图中的所有基本环。

    restrict_to: 只枚举至少包含该集合中一个节点的环（增量更新用）。
    """
    internal = [n for n in graph.nodes if is_internal(n)]
    adj: Dict[str, List[str]] = {
        v: sorted(w for w in graph.successors(v) if is_internal(w))
        for v in internal
    }
    # 自环
    cycles: List[Cycle] = []
    self_loop_nodes = set()
    for v in internal:
        if v in adj[v]:
            self_loop_nodes.add(v)
            cycles.append(_make_cycle(graph, [v]))
    # 非平凡 SCC 上的 Johnson
    sccs = _tarjan_scc(internal, lambda v: adj.get(v, []))
    all_paths: List[List[str]] = []
    for comp in sccs:
        if len(comp) < 2:
            continue
        if restrict_to is not None and not (set(comp) & restrict_to):
            continue
        comp_sorted = sorted(comp)
        sub_adj = {v: [w for w in adj[v] if w in comp_set] for v, comp_set in
                   ((x, set(comp)) for x in comp_sorted)}
        for path in _johnson_circuits(comp_sorted, sub_adj):
            all_paths.append(path)
    # restrict 语义：只保留至少包含一个目标节点的环
    if restrict_to is not None:
        all_paths = [p for p in all_paths if restrict_to & set(p)]
    # 自环的 restrict 过滤
    if restrict_to is not None:
        cycles = [c for c in cycles if set(c.path) & restrict_to]
    seen = set()
    for path in all_paths:
        canon = _canonical(path)
        if canon in seen:
            continue
        seen.add(canon)
        cycles.append(_make_cycle(graph, list(canon)))
    cycles.sort(key=lambda c: (c.length, c.path))
    return cycles


def _make_cycle(graph: CallGraph, path: List[str]) -> Cycle:
    edges = tuple(
        (path[i], path[(i + 1) % len(path)]) for i in range(len(path))
    )
    certain = all(graph.edge_certain(u, v) for u, v in edges)
    return Cycle(
        path=tuple(path),
        kind="self-recursion" if len(path) == 1 else "mutual-recursion",
        confidence="certain" if certain else "potential",
        edges=edges,
    )


def verify_cycle(graph: CallGraph, cycle: Cycle) -> bool:
    """验证环路径：每个相邻调用边（含末节点到首节点）在图中真实存在。"""
    path = cycle.path
    if not path:
        return False
    for i in range(len(path)):
        u, v = path[i], path[(i + 1) % len(path)]
        if not graph.has_edge(u, v):
            return False
    return True
