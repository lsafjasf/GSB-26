"""缺陷版本：仅用于在修复前稳定复现现网四类问题。

对照真实代码中常见的错误写法：
  缺陷 1（栈溢出）：_visit 是递归实现，十万节点链直接 RecursionError。
  缺陷 2（环重复访问）：进入时虽检查 visited，却在“离开节点”时才打标记；
         环路上的节点尚未标记，被递归重复进入直至栈溢出，
         同一条边也随之被反复处理。
  缺陷 3（并行/无向边重复处理）：无向边在两个端点的邻接表里各存一份，
         没有按 edge_id 去重，同一条边被 on_edge 处理两次。
  缺陷 4（异常污染）：visited 是 walker 实例属性且跨 walk 复用，
         遍历中途抛异常后，已完成子树的标记残留在 visited 中，
         再次遍历时这些节点被直接跳过，结果与全新遍历不一致。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from dfs import Edge, Graph, WalkResult


class BuggyDFSWalker:
    def __init__(
        self,
        graph: Graph,
        on_node: Optional[Callable[[Any], None]] = None,
        on_edge: Optional[Callable[[Edge], None]] = None,
    ) -> None:
        self.graph = graph
        self.on_node = on_node
        self.on_edge = on_edge
        self.visited: set = set()  # 缺陷 4：跨 walk 复用，异常后不清理

    def walk(self, start: Any) -> WalkResult:
        node_order: List[Any] = []
        edge_order: List[Edge] = []

        def visit(node: Any) -> None:
            if node in self.visited:
                return
            for edge in self.graph.edges_from(node):
                if self.on_edge is not None:
                    self.on_edge(edge)  # 缺陷 3：无向边反向副本也被处理
                edge_order.append(edge)
                visit(edge.v)  # 缺陷 1：深度随链长增长；缺陷 2：环上重复进入
            self.visited.add(node)  # 缺陷 2：离开时才标记（太晚了）
            node_order.append(node)
            if self.on_node is not None:
                self.on_node(node)

        visit(start)
        return WalkResult(node_order, edge_order, True)


class BuggyMarkOnEntryWalker(BuggyDFSWalker):
    """另一种常见缺陷写法：进入时标记（环不再递归爆炸），但：
      - 仍是递归实现（缺陷 1 依旧）；
      - 无向边不按 edge_id 去重（缺陷 3 可被干净复现）；
      - visited 仍跨 walk 复用（缺陷 4 依旧）。
    """

    def walk(self, start: Any) -> WalkResult:
        node_order: List[Any] = []
        edge_order: List[Edge] = []

        def visit(node: Any) -> None:
            if node in self.visited:
                return
            self.visited.add(node)
            node_order.append(node)
            if self.on_node is not None:
                self.on_node(node)
            for edge in self.graph.edges_from(node):
                if self.on_edge is not None:
                    self.on_edge(edge)
                edge_order.append(edge)
                visit(edge.v)

        visit(start)
        return WalkResult(node_order, edge_order, True)
