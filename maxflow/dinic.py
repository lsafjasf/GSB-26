"""最大流 / 最小割库（仅依赖 Python 标准库）。

算法：Dinic（BFS 分层 + 当前弧优化的阻塞流），DFS 采用显式栈实现，
图很大时不受 Python 递归深度限制。容量使用 Python 任意精度整数，
不存在溢出问题（10**1000 量级也可直接使用）。
"""

from collections import deque
from numbers import Integral


class Edge(tuple):
    """用户视角的原始有向边：(id, u, v, capacity)。"""

    __slots__ = ()

    def __new__(cls, eid, u, v, capacity):
        return tuple.__new__(cls, (eid, u, v, capacity))

    def __repr__(self):
        return f"Edge(id={self[0]}, {self[1]} -> {self[2]}, cap={self[3]})"

    @property
    def id(self):
        return self[0]

    @property
    def u(self):
        return self[1]

    @property
    def v(self):
        return self[2]

    @property
    def capacity(self):
        return self[3]


class FlowResult(tuple):
    """max_flow 的返回值：流值、最小割边集合、割容量（流值 == 割容量）。"""

    __slots__ = ()

    def __new__(cls, value, cut, cut_capacity):
        return tuple.__new__(cls, (value, tuple(cut), cut_capacity))

    def __repr__(self):
        return (f"FlowResult(value={self[0]}, cut_capacity={self[2]}, "
                f"cut={list(self[1])})")

    @property
    def value(self):
        return self[0]

    @property
    def cut(self):
        return self[1]

    @property
    def cut_capacity(self):
        return self[2]


class _Arc:
    """残量网络中的一条弧。"""

    __slots__ = ("to", "rev", "cap", "eid")

    def __init__(self, to, rev, cap, eid):
        self.to = to          # 弧头
        self.rev = rev        # 反向弧在 adj[to] 中的下标
        self.cap = cap        # 残量容量
        self.eid = eid        # 对应的原始边 id（反向残量弧为 -1）


def _check_vertex(x, n, name):
    if isinstance(x, bool) or not isinstance(x, int):
        raise TypeError(f"{name} 必须是 int，得到 {type(x).__name__}")
    if x < 0 or x >= n:
        raise ValueError(f"{name}={x} 越界，合法范围 [0, {n - 1}]")
    return x


def _check_capacity(c):
    if isinstance(c, bool) or not isinstance(c, Integral):
        raise TypeError(f"容量必须是整数，得到 {type(c).__name__}")
    if c < 0:
        raise ValueError(f"容量不能为负，得到 {c}")
    return c


class MaxFlow:
    """Dinic 最大流求解器。

    用法::

        mf = MaxFlow(n)
        eid = mf.add_edge(u, v, capacity)
        value = mf.max_flow(s, t)
        cut = mf.min_cut()          # 最小割的原始边集合
        mf.cut_capacity()           # 割容量，恒等于 value
        mf.flow_on(eid)             # 每条边上的流量
    """

    def __init__(self, n):
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError("顶点数 n 必须是 int")
        if n < 0:
            raise ValueError("顶点数 n 不能为负")
        self.n = n
        self._adj = [[] for _ in range(n)]
        self._edges = []          # Edge，按添加顺序
        self._arc_loc = []        # 每条原始边对应的正向弧位置 (u, idx)，自环为 None
        self._s = None
        self._t = None
        self._reach = None        # 最大流后残量网络中从 s 可达的顶点
        self._flow = None

    # -------------------------------------------------- 构图
    def add_edge(self, u, v, capacity):
        """添加有向边 u -> v，容量 capacity（允许为 0）。返回边 id。

        - 自环 (u == v) 对 s-t 流与 s-t 割均无贡献，直接忽略但仍分配 id；
        - 反平行边、重边可任意添加，每条边独立计容量。
        """
        u = _check_vertex(u, self.n, "u")
        v = _check_vertex(v, self.n, "v")
        capacity = _check_capacity(capacity)
        eid = len(self._edges)
        self._edges.append(Edge(eid, u, v, capacity))

        if u == v:
            self._arc_loc.append(None)
            return eid

        fwd_idx = len(self._adj[u])
        rev_idx = len(self._adj[v])
        self._adj[u].append(_Arc(v, rev_idx, capacity, eid))
        self._adj[v].append(_Arc(u, fwd_idx, 0, -1))
        self._arc_loc.append((u, fwd_idx))
        return eid

    # -------------------------------------------------- 求解
    def max_flow(self, s, t):
        """计算 s -> t 的最大流值，并缓存最小割信息。

        s == t 时按定义流值为 0、割为空集（割只在 s != t 时有分离意义）。
        """
        s = _check_vertex(s, self.n, "s")
        t = _check_vertex(t, self.n, "t")
        self._s, self._t = s, t

        if s == t:
            self._flow = 0
            self._reach = [i == s for i in range(self.n)]
            return 0

        adj, n = self._adj, self.n
        total = 0  # 本次调用新增的流量；self._flow 累计全部历史调用

        while True:
            # BFS 建立分层图
            level = [-1] * n
            level[s] = 0
            queue = deque([s])
            while queue:
                u = queue.popleft()
                nu = level[u]
                for arc in adj[u]:
                    if arc.cap > 0 and level[arc.to] < 0:
                        level[arc.to] = nu + 1
                        queue.append(arc.to)
            if level[t] < 0:
                break

            # 当前弧迭代式 DFS，反复增广直到阻塞
            cur = [0] * n
            while True:
                pushed = self._augment(s, t, level, cur)
                if pushed == 0:
                    break
                total += pushed

        self._flow = (self._flow or 0) + total
        self._reach = self._reachable(s)
        return self._flow

    def _augment(self, s, t, level, cur):
        """在分层图上找一条 s->t 增广路并推送瓶颈流量，显式栈实现。"""
        adj = self._adj
        vertices = [s]
        path = []  # 与 vertices 对齐：path[i] 是 vertices[i] -> vertices[i+1] 的弧

        while vertices:
            u = vertices[-1]

            if u == t:
                bottleneck = None
                for arc in path:
                    bottleneck = arc.cap if bottleneck is None else min(bottleneck, arc.cap)
                for arc in path:
                    arc.cap -= bottleneck
                    adj[arc.to][arc.rev].cap += bottleneck
                return bottleneck

            arcs = adj[u]
            i = cur[u]
            while i < len(arcs):
                arc = arcs[i]
                if arc.cap > 0 and level[arc.to] == level[u] + 1:
                    break
                i += 1
            cur[u] = i + 1 if i < len(arcs) else i

            if i < len(arcs):
                vertices.append(arc.to)
                path.append(arc)
            else:
                # u 在当前分层图中到不了 t，标记后回溯（当前弧/死路剪枝）
                level[u] = -1
                vertices.pop()
                if path:
                    path.pop()

        return 0

    def _reachable(self, s):
        """最大流后在残量网络中从 s 做 BFS。"""
        seen = [False] * self.n
        seen[s] = True
        queue = deque([s])
        adj = self._adj
        while queue:
            u = queue.popleft()
            for arc in adj[u]:
                if arc.cap > 0 and not seen[arc.to]:
                    seen[arc.to] = True
                    queue.append(arc.to)
        return seen

    # -------------------------------------------------- 结果
    def min_cut(self):
        """返回最小割 (S, T) 中从 S 指向 T 的原始边列表。

        S = 最大流后残量网络中从源点可达的顶点集合。
        容量为 0 的跨割边同样包含在集合中（容量贡献为 0）。
        需先调用 max_flow。
        """
        if self._reach is None:
            raise RuntimeError("请先调用 max_flow(s, t)")
        if self._s == self._t:
            return []
        return [e for e in self._edges if self._reach[e.u] and not self._reach[e.v]]

    def cut_capacity(self, cut=None):
        """给定割边集合的容量；默认为 min_cut()，其容量等于最大流值。"""
        if cut is None:
            cut = self.min_cut()
        return sum(e.capacity for e in cut)

    def flow_on(self, eid):
        """返回编号 eid 的原始边上当前的净流量。"""
        if not (0 <= eid < len(self._edges)):
            raise IndexError(f"边 id {eid} 不存在")
        loc = self._arc_loc[eid]
        if loc is None:
            return 0  # 自环
        u, idx = loc
        return self._edges[eid].capacity - self._adj[u][idx].cap

    def flows(self):
        """返回每条边的流量列表（下标即边 id，自环恒为 0）。"""
        return [self.flow_on(i) for i in range(len(self._edges))]

    def source_set(self):
        """返回最小割源侧顶点集合 S（布尔数组）。"""
        if self._reach is None:
            raise RuntimeError("请先调用 max_flow(s, t)")
        return list(self._reach)

    def solve(self, s, t):
        """一步得到 FlowResult(value, cut, cut_capacity)。"""
        value = self.max_flow(s, t)
        cut = self.min_cut()
        return FlowResult(value, cut, sum(e.capacity for e in cut))


def max_flow(n, edges, s=0, t=None):
    """便捷函数：给定顶点数与 (u, v, capacity) 边列表，直接求最大流。

    返回 FlowResult(value, cut, cut_capacity)，其中 value == cut_capacity。
    """
    if t is None:
        t = n - 1
    mf = MaxFlow(n)
    for edge in edges:
        u, v, capacity = edge
        mf.add_edge(u, v, capacity)
    return mf.solve(s, t)
