"""深度优先图遍历（迭代实现，仅依赖标准库）。

本迭代在“不递归 / 节点与边各触发一次 / 异常可清理”的基础上，明确并实现了
**稳定遍历顺序** 与 **重复边、自环去重策略**，使“同一图 + 同一配置”无论边
以何种顺序录入，遍历得到的节点序与边序都**逐条一致、完全可复现**。

--------------------------------------------------------------------------------
稳定遍历顺序规则（默认配置，实际输出严格按此执行，可逐条核对）
--------------------------------------------------------------------------------
1. 邻接表顺序：建图后调用 ``freeze()``（``walk`` 时若未冻结会自动冻结）会对
   每个节点的邻接边**显式排序**，排序键为
       (目标节点序键, 边 id 序键)
   的全序升序。因此遍历顺序只取决于 (图, 配置)，与 ``add_edge`` 的录入先后无关。
2. 序键（``OrderPolicy.SORTED``，默认）：默认键函数把节点 / 边 id 映射为
       (类型名, 原值)
   —— 同为 int（含 bool）时按数值大小；同为 str 时按字典序；不同类型按类型名
   分段，避免 ``3 < "a"`` 这类跨类型 TypeError。可通过 ``order_key`` 自定义。
   另有 ``OrderPolicy.INSERTION``：保留 ``add_edge`` 录入顺序（旧行为，不排序）。
3. 节点序（先序 DFS + 发现即标记 gray-marking）：
   - 单源：起点最先访问；处理边 (u,v) 时若 v 尚未发现则立即深入 v，
     v 的全部邻接边先于 u 的后续邻接边处理，回溯后再继续。
   - 全图（``walk(start=None)`` / ``walk_all``）：源按“节点序键升序”依次选取，
     每个连通/弱连通分量内部再按上述先序 DFS。
4. 边序：每条逻辑边在**第一次被看到**时触发一次 ``on_edge``（去重后），
   触发先后即边序。同一源下相同 (图, 配置) 边序唯一确定。

--------------------------------------------------------------------------------
重复边 / 自环去重策略（``duplicate_policy``）
--------------------------------------------------------------------------------
- ``"multigraph"``（默认）：不去重。边以 **edge_id 为唯一身份**；相同 (u, v)
  之间的并行边只要 edge_id 不同就各保留、各触发一次，邻接表内按
  (目标键, edge_id 键) 稳定排序。edge_id 不允许重复（重复录入直接 ValueError）。
- ``"simple"``：折叠为简单图。同一节点对只保留 edge_id 序键最小的一条：
  * 有向图按有序对 (u, v) 折叠；
  * 无向图按无序对 {u, v} 折叠（端点先按序键规范为 (lo, hi)）；
  * 自环 (u, u) 同样按 (u, u) / {u} 折叠，最多保留一条。
- 自环：永远只在 u 的邻接表中存 **1 份**（无向也不镜像复制），因此最多看到 1 次。
- 无向普通边：在两个端点邻接表各存 1 份镜像，遍历时按 edge_id 去重（每条
  逻辑边恰好 1 次）；有向边只存 1 份，不需要该集合（千万边省数百 MB）。
"""

from __future__ import annotations

from enum import Enum
import functools
from collections import namedtuple
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

Edge = namedtuple("Edge", ["id", "u", "v"])
WalkResult = namedtuple("WalkResult", ["nodes", "edges", "completed"])


class OrderPolicy(Enum):
    """邻接表排序策略。"""

    SORTED = "sorted"        # 默认：按 (目标节点键, 边 id 键) 显式排序
    INSERTION = "insertion"  # 旧行为：保留 add_edge 录入顺序


def default_order_key(value: Any) -> Tuple[str, Any]:
    """默认全序键：同类型按原值比较，跨类型按类型名分段。

    返回 (类型名, 原值)。bool 归入 int 段（Python 中 bool 是 int 子类），
    使 True/False 能与整数一起稳定排序。
    """

    if isinstance(value, bool):
        return ("int", int(value))
    return (type(value).__name__, value)


@functools.total_ordering
class _Key:
    """把任意值包成“可比较且不抛异常”的全序键（用 id 兜底不可比对象）。"""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def _cmp(self, other: "_Key") -> Tuple[int, Any]:
        a, b = self.value, other.value
        ta, tb = type(a).__name__, type(b).__name__
        if ta != tb:
            return (-1, None) if ta < tb else (1, None)
        try:
            if a < b:
                return (-1, None)
            if a > b:
                return (1, None)
            return (0, None)
        except TypeError:
            ia, ib = id(a), id(b)
            return (-1, None) if ia < ib else (1, None) if ia > ib else (0, None)

    def __lt__(self, other: "_Key") -> bool:
        return self._cmp(other)[0] < 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Key) and self._cmp(other)[0] == 0


class Graph:
    """带边 id 的（有向/无向）（多重/简单）邻接表。

    参数:
        directed:         True 有向 / False 无向（无向边两端各存镜像）。
        order:            OrderPolicy.SORTED（默认，显式排序）或 INSERTION。
        duplicate_policy: "multigraph"（默认，保留并行边）或 "simple"（折叠）。
        order_key:        自定义序键函数，默认 default_order_key。
    """

    def __init__(
        self,
        directed: bool = False,
        *,
        order: OrderPolicy = OrderPolicy.SORTED,
        duplicate_policy: str = "multigraph",
        order_key: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        if duplicate_policy not in ("multigraph", "simple"):
            raise ValueError(f"unknown duplicate_policy: {duplicate_policy!r}")
        if not isinstance(order, OrderPolicy):
            raise TypeError(f"order must be an OrderPolicy, got {order!r}")
        self.directed = directed
        self.order = order
        self.duplicate_policy = duplicate_policy
        self.order_key = order_key or default_order_key
        self._adj: Dict[Any, List[Edge]] = {}
        self._frozen_adj: Optional[Dict[Any, List[Edge]]] = None
        self._node_order: List[Any] = []
        self._edge_ids: set = set()
        self._edge_count: int = 0  # 逻辑边条数（simple 折叠后随之减少）

    # -- 构建 ---------------------------------------------------------------
    def add_node(self, node: Any) -> None:
        if node not in self._adj:
            self._adj[node] = []
            self._node_order.append(node)
        self._frozen_adj = None

    def add_edge(self, edge_id: Any, u: Any, v: Any) -> None:
        if edge_id in self._edge_ids:
            raise ValueError(f"duplicate edge_id not allowed: {edge_id!r}")
        self._edge_ids.add(edge_id)
        self.add_node(u)
        self.add_node(v)
        self._adj[u].append(Edge(edge_id, u, v))
        if not self.directed and u != v:
            self._adj[v].append(Edge(edge_id, v, u))
        self._edge_count += 1
        self._frozen_adj = None

    # -- 冻结 / 规范化：排序 + simple 折叠 ----------------------------------
    def freeze(self) -> "Graph":
        """规范化图：按策略折叠重复边，并对邻接表显式排序（幂等）。

        节点键只对每个节点求一次并缓存；边 id 键是排序时的临时对象，
        不在遍历期间留存（避免千万边下产生千万个长期存活的键对象）。
        """
        if self._frozen_adj is not None:
            return self
        # 节点键：每个节点求一次。边 id 键默认作为排序临时对象（Timsort 的
        # 键只对每个元素求一次，算完即释放，不留千万级长寿命对象）；仅在
        # simple 折叠时才需要稳定的 id->键 映射做跨边比较。
        node_keys = {n: _Key(self.order_key(n)) for n in self._adj}
        edge_id_keys: Dict[Any, "_Key"] = (
            {eid: _Key(self.order_key(eid)) for eid in self._edge_ids}
            if self.duplicate_policy == "simple" else {})

        if self.duplicate_policy == "simple":
            # 全局按“规范节点对”折叠，每对仅保留 edge_id 键最小的一条；
            # 无向镜像在 _adj 中出现两次但规范对相同，天然归并为一条。
            best: Dict[Tuple[Any, Any], Edge] = {}
            best_id_keys: Dict[Tuple[Any, Any], _Key] = {}
            for u, edges in self._adj.items():
                for e in edges:
                    if self.directed:
                        pair, ce = (u, e.v), Edge(e.id, u, e.v)
                    else:
                        if node_keys[u] <= node_keys[e.v]:
                            lo, hi = u, e.v
                        else:
                            lo, hi = e.v, u
                        pair, ce = (lo, hi), Edge(e.id, lo, hi)
                    ek = edge_id_keys[e.id]
                    cur = best_id_keys.get(pair)
                    if cur is None or ek < cur:
                        best[pair] = ce
                        best_id_keys[pair] = ek
            frozen = {n: [] for n in self._adj}
            for (a, b), e in best.items():
                frozen[a].append(Edge(e.id, a, b))
                if not self.directed and a != b:
                    frozen[b].append(Edge(e.id, b, a))
            self._edge_count = len(best)
        else:
            frozen = {u: list(edges) for u, edges in self._adj.items()}

        if self.order is OrderPolicy.SORTED:
            if self.duplicate_policy == "simple":
                for u, edges in frozen.items():
                    edges.sort(key=lambda e, nk=node_keys,
                                      ek=edge_id_keys: (nk[e.v], ek[e.id]))
            else:
                for u, edges in frozen.items():
                    edges.sort(key=lambda e, nk=node_keys: (
                        nk[e.v], _Key(self.order_key(e.id))))

        self._frozen_adj = frozen
        self._frozen_node_order = sorted(
            self._adj, key=lambda n, nk=node_keys: nk[n])
        return self

    # -- 查询 ---------------------------------------------------------------
    def nodes(self) -> List[Any]:
        """节点录入顺序（插入序）。"""
        return list(self._node_order)

    def ordered_nodes(self) -> List[Any]:
        """节点序键升序（全图遍历的选源顺序）。"""
        self.freeze()
        return list(self._frozen_node_order)

    def edges_from(self, node: Any) -> List[Edge]:
        """规范化（排序 + 折叠）后的邻接边；未知节点返回空。"""
        self.freeze()
        return self._frozen_adj.get(node, ())

    @property
    def num_nodes(self) -> int:
        return len(self._adj)

    @property
    def num_edges(self) -> int:
        """逻辑边条数（simple 为折叠后）。"""
        self.freeze()
        return self._edge_count


class TraversalAborted(Exception):
    """用于测试：模拟遍历中途由外部回调抛出的异常。"""


class DFSWalker:
    """可复用的先序 DFS 遍历器。遍历状态是每次 walk 的临时状态。"""

    def __init__(
        self,
        graph: Graph,
        on_node: Optional[Callable[[Any], None]] = None,
        on_edge: Optional[Callable[[Edge], None]] = None,
    ) -> None:
        self.graph = graph
        self.on_node = on_node
        self.on_edge = on_edge
        self._active_visited: Optional[set] = None
        self._active_stack: Optional[list] = None

    def walk_all(
        self,
        *,
        track_nodes: bool = True,
        track_edges: bool = True,
    ) -> WalkResult:
        """遍历全图：按节点序键升序选源（含不连通分量）。"""
        return self.walk(
            None, track_nodes=track_nodes, track_edges=track_edges)

    def walk(
        self,
        start: Any = None,
        *,
        track_nodes: bool = True,
        track_edges: bool = True,
    ) -> WalkResult:
        """从 start 先序 DFS；start=None 时按序键升序选源遍历全图。

        回调抛出的异常原样传播，但临时遍历状态一定在传播前清理完毕。
        """
        self.graph.freeze()
        adj = self.graph._frozen_adj
        visited: set = set()
        stack: List[Iterator[Edge]] = []
        self._active_visited = visited
        self._active_stack = stack
        # 无向边两端各存镜像，按 edge_id 去重；有向边只存一份，省去该集合。
        seen_edges: Optional[set] = set() if not self.graph.directed else None
        node_order: List[Any] = []
        edge_order: List[Edge] = []
        completed = False
        sources: Iterable[Any]
        sources = (start,) if start is not None else self.graph._frozen_node_order
        try:
            for root in sources:
                if root in visited or root not in adj:
                    continue
                visited.add(root)
                self._fire_node(root, node_order, track_nodes)
                stack.append(iter(adj[root]))
                while stack:
                    edge = next(stack[-1], None)
                    if edge is None:
                        stack.pop()
                        continue
                    if seen_edges is not None and edge.id in seen_edges:
                        continue
                    if seen_edges is not None:
                        seen_edges.add(edge.id)
                    self._fire_edge(edge, edge_order, track_edges)
                    nxt = edge.v
                    if nxt not in visited:
                        visited.add(nxt)  # 发现即标记：任一节点最多入栈一次
                        self._fire_node(nxt, node_order, track_nodes)
                        stack.append(iter(adj[nxt]))
            completed = True
        finally:
            visited.clear()
            if seen_edges is not None:
                seen_edges.clear()
            stack.clear()
            self._active_visited = None
            self._active_stack = None
        return WalkResult(node_order, edge_order, completed)

    def _fire_node(self, node: Any, order: List[Any], track: bool) -> None:
        if track:
            order.append(node)
        if self.on_node is not None:
            self.on_node(node)

    def _fire_edge(self, edge: Edge, order: List[Edge], track: bool) -> None:
        if track:
            order.append(edge)
        if self.on_edge is not None:
            self.on_edge(edge)

    def is_idle(self) -> bool:
        """遍历器当前不持有任何遍历状态（异常清理断言用）。"""
        return self._active_visited is None and self._active_stack is None
