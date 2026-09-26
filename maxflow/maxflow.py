"""maxflow.py — 最大流 / 最小割库（Dinic 算法，仅标准库）

算法：Dinic（BFS 分层 + DFS 阻塞流，当前弧优化）
复杂度：一般图 O(V^2 * E)；单位容量图 O(E * sqrt(V))；
        二分匹配 O(E * sqrt(V))。

设计要点：
- 反平行边 (u->v 与 v->u 同时存在)：每条原始边独立建正/反两条残量边，
  互不干扰，天然支持。
- 自环 / 零容量边：允许加入，分层图中不会被使用，不影响结果。
- 超大容量：Python int 无溢出；内部 INF 取所有容量之和，保持整数运算。
- s == t：按约定返回流值 0 与空割（无 s-t 流可定义）。
"""

import sys
from collections import deque


class _Edge:
    __slots__ = ("to", "rev", "cap")

    def __init__(self, to, rev, cap):
        self.to = to          # 终点
        self.rev = rev        # 在 to 的邻接表中反向边的下标
        self.cap = cap        # 残量容量


class MaxFlow:
    def __init__(self, n):
        if n <= 0:
            raise ValueError("n must be positive")
        self.n = n
        self.g = [[] for _ in range(n)]
        self._orig = []       # 原始边: (u, v, cap, 正向边对象)

    def add_edge(self, u, v, cap):
        """添加容量为 cap 的有向边 u->v，返回边的编号（按加入顺序）。"""
        if not (0 <= u < self.n and 0 <= v < self.n):
            raise ValueError("vertex out of range")
        if cap < 0:
            raise ValueError("capacity must be non-negative")
        fwd = _Edge(v, len(self.g[v]), cap)
        rev = _Edge(u, len(self.g[u]), 0)
        self.g[u].append(fwd)
        self.g[v].append(rev)
        self._orig.append((u, v, cap, fwd))
        return len(self._orig) - 1

    # ---- Dinic ----

    def _bfs_levels(self, s, t):
        level = [-1] * self.n
        level[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for e in self.g[u]:
                if e.cap > 0 and level[e.to] < 0:
                    level[e.to] = level[u] + 1
                    q.append(e.to)
        return level

    def _dfs_blocking(self, u, t, f, level, it):
        if u == t:
            return f
        gu = self.g[u]
        while it[u] < len(gu):
            e = gu[it[u]]
            if e.cap > 0 and level[e.to] == level[u] + 1:
                d = self._dfs_blocking(e.to, t, min(f, e.cap), level, it)
                if d > 0:
                    e.cap -= d
                    self.g[e.to][e.rev].cap += d
                    return d
            it[u] += 1
        return 0

    def max_flow(self, s, t):
        """返回 s->t 最大流值。可重复调用（在残量网络上继续增广）。"""
        if not (0 <= s < self.n and 0 <= t < self.n):
            raise ValueError("vertex out of range")
        if s == t:
            return 0
        # DFS 递归深度可能达到 O(V)，按需上调
        need = 4 * self.n + 100
        if sys.getrecursionlimit() < need:
            sys.setrecursionlimit(need)
        # INF = 所有原始容量之和（有限、不溢出，纯整数）
        inf = sum(c for _, _, c, _ in self._orig) or 1
        flow = 0
        while True:
            level = self._bfs_levels(s, t)
            if level[t] < 0:
                return flow
            it = [0] * self.n
            while True:
                f = self._dfs_blocking(s, t, inf, level, it)
                if f == 0:
                    break
                flow += f

    # ---- 最小割 ----

    def _reachable_from(self, s):
        seen = [False] * self.n
        seen[s] = True
        stack = [s]
        while stack:
            u = stack.pop()
            for e in self.g[u]:
                if e.cap > 0 and not seen[e.to]:
                    seen[e.to] = True
                    stack.append(e.to)
        return seen

    def min_cut(self, s, t):
        """在残量网络上求最小割。

        返回 (cut_value, cut_edges, S)：
        - cut_value: 割容量（等于最大流值，须先调用 max_flow）
        - cut_edges: 原始边中所有 S->T 的边 [(u, v, cap), ...]
        - S: 源侧顶点集合（list[bool]）
        """
        if s == t:
            return 0, [], [False] * self.n
        seen = self._reachable_from(s)
        cut_edges = []
        cut_value = 0
        for u, v, cap, _fwd in self._orig:
            if seen[u] and not seen[v]:
                cut_value += cap
                if cap > 0:  # 零容量边不计入割边集合（不影响割容量）
                    cut_edges.append((u, v, cap))
        return cut_value, cut_edges, seen

    def flow_edges(self):
        """返回所有承载正流量的原始边 [(u, v, flow), ...]。"""
        out = []
        for u, v, cap, fwd in self._orig:
            f = cap - fwd.cap
            if f > 0:
                out.append((u, v, f))
        return out
