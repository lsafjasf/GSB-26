"""深度优先图遍历（迭代实现，仅依赖标准库）。

修复后的实现满足以下不变量（见 test_dfs.py）：
  1. 不依赖递归深度：显式栈迭代，可遍历任意长度链。
  2. 每个可达节点的 on_node 恰好触发一次（环不会导致重复访问）。
  3. 每条边（以边 id 计，含并行边）的 on_edge 恰好触发一次。
  4. 无论通过哪条路径抛出异常，遍历器内部状态都会被清理，
     随后再次遍历与“全新遍历”结果完全一致。

遍历顺序规则（结果可复现）：
  - 图中的节点顺序为 add_node 的插入顺序（邻接表以 dict 保存）。
  - 每个节点的邻接边顺序为 add_edge 的插入顺序（稳定、确定）。
  - 采用“先序 DFS + 发现时标记（gray 标记）”：
      * 起点最先访问；
      * 进入节点 v 时按邻接表插入顺序依次处理边；
      * 沿当前边到达的新节点立即入栈，且该节点的边排在栈中
        其后邻接边之前处理（即标准 DFS 的先深入、再回溯顺序）；
      * 节点在“发现”（入栈）时标记，而不是“离开”（出栈）时标记，
        因此菱形/环中同一节点只会被入栈一次。
  - undirected 边即使从两个端点分别被看到，也只处理一次。
  - 多重图：相同 (u, v) 之间的并行边具有不同 edge_id，各自处理一次。
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

Edge = namedtuple("Edge", ["id", "u", "v"])
WalkResult = namedtuple("WalkResult", ["nodes", "edges", "completed"])


class Graph:
    """带边 id 的（有向/无向）多重邻接表。

    节点必须可哈希；邻接顺序等于插入顺序，故遍历顺序完全确定。
    """

    def __init__(self, directed: bool = False) -> None:
        self.directed = directed
        self._adj: Dict[Any, List[Edge]] = {}

    def add_node(self, node: Any) -> None:
        self._adj.setdefault(node, [])

    def add_edge(self, edge_id: Any, u: Any, v: Any) -> None:
        self.add_node(u)
        self.add_node(v)
        self._adj[u].append(Edge(edge_id, u, v))
        if not self.directed and u != v:
            self._adj[v].append(Edge(edge_id, v, u))

    def nodes(self) -> List[Any]:
        return list(self._adj.keys())

    def edges_from(self, node: Any) -> List[Edge]:
        return self._adj.get(node, ())


class TraversalAborted(Exception):
    """用于测试：模拟遍历中途由外部回调抛出的异常。"""


class DFSWalker:
    """可复用的 DFS 遍历器。遍历状态是每次 walk 的临时状态。"""

    def __init__(
        self,
        graph: Graph,
        on_node: Optional[Callable[[Any], None]] = None,
        on_edge: Optional[Callable[[Edge], None]] = None,
    ) -> None:
        self.graph = graph
        self.on_node = on_node
        self.on_edge = on_edge
        # 仅用于断言“异常后状态已清理”；遍历中为临时集合，空闲时为空。
        self._active_visited: Optional[set] = None
        self._active_stack: Optional[list] = None

    def walk(
        self,
        start: Any,
        *,
        track_nodes: bool = True,
        track_edges: bool = True,
    ) -> WalkResult:
        """从 start 执行一次 DFS。

        回调抛出的任何异常都会原样向上传播，但临时遍历状态
        （已发现集合、显式栈）一定在异常传播前清理完毕。
        """
        visited: set = set()
        stack: List[Iterator[Edge]] = []
        self._active_visited = visited
        self._active_stack = stack
        # 无向边在两个端点的邻接表中各存一份，必须按 edge_id 去重；
        # 有向边只存一份，不可能重复，直接省去这个集合（千万边级别可省数百 MB）。
        seen_edges: Optional[set] = set() if not self.graph.directed else None
        node_order: List[Any] = []
        edge_order: List[Edge] = []
        completed = False
        try:
            # 标记时机 = 发现（入栈）时，保证每个节点只入栈一次。
            visited.add(start)
            self._fire_node(start, node_order, track_nodes)
            stack.append(iter(self.graph.edges_from(start)))
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
                    visited.add(nxt)
                    self._fire_node(nxt, node_order, track_nodes)
                    stack.append(iter(self.graph.edges_from(nxt)))
            completed = True
        finally:
            # 异常路径也必须清理，避免污染后续遍历。
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
