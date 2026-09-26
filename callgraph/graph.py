"""调用图数据结构。

节点三类：
- 内部函数：全限定名（如 "foo"、"C.m"、"outer.inner"）
- 外部节点："<external:...>"（外部库/内建/构造调用）
- 合成未知节点："<unknown:...>"（无法静态确定的调用目标，每个调用点一个）

边按 (caller, callee) 去重；只要有一个调用点不确定，该边即不确定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

EXTERNAL_PREFIX = "<external:"
UNKNOWN_PREFIX = "<unknown:"


def is_internal(node: str) -> bool:
    return not node.startswith("<")


@dataclass
class EdgeInfo:
    certain: bool = True
    kinds: Set[str] = field(default_factory=set)
    count: int = 0

    def merge(self, certain: bool, kind: str) -> None:
        self.certain = self.certain and certain
        self.kinds.add(kind)
        self.count += 1


class CallGraph:
    def __init__(self) -> None:
        self.nodes: Set[str] = set()
        self.edges: Dict[str, Set[str]] = {}
        self.edge_info: Dict[Tuple[str, str], EdgeInfo] = {}
        self.in_degree: Dict[str, int] = {}

    # ---- 节点分类 ----
    @staticmethod
    def is_internal(node: str) -> bool:
        return is_internal(node)

    def internal_nodes(self) -> List[str]:
        return [n for n in self.nodes if is_internal(n)]

    # ---- 增删 ----
    def add_call(self, caller: str, callee: str, certain: bool = True,
                 kind: str = "direct") -> None:
        self.nodes.add(caller)
        self.nodes.add(callee)
        succ = self.edges.setdefault(caller, set())
        if callee not in succ:
            succ.add(callee)
            self.in_degree[callee] = self.in_degree.get(callee, 0) + 1
            self.in_degree.setdefault(caller, self.in_degree.get(caller, 0))
            self.edge_info[(caller, callee)] = EdgeInfo(certain, {kind}, 1)
        else:
            self.edge_info[(caller, callee)].merge(certain, kind)

    def replace_calls(self, caller: str, calls: Iterable) -> None:
        """移除 caller 的全部出边，按 calls（CallSite 或 4 元组）重建。"""
        for callee in list(self.edges.get(caller, ())):
            self._remove_edge(caller, callee)
        self.nodes.add(caller)
        self.in_degree.setdefault(caller, self.in_degree.get(caller, 0))
        for c in calls:
            if hasattr(c, "target"):
                self.add_call(caller, c.target, c.certain, c.kind)
            else:
                tgt, certain, kind = c[0], c[1], (c[2] if len(c) > 2 else "direct")
                self.add_call(caller, tgt, certain, kind)

    def remove_function(self, fqn: str) -> None:
        """删除函数节点及其出边；入边保留，目标转为外部节点。"""
        # 先重写指向它的入边
        for caller in list(self.edges.keys()):
            if fqn in self.edges[caller]:
                info = self.edge_info.pop((caller, fqn))
                self._remove_edge(caller, fqn)
                ext = f"{EXTERNAL_PREFIX}{fqn}>"
                self.add_call(caller, ext, info.certain, next(iter(info.kinds), "direct"))
        # 再删除其出边与节点
        for callee in list(self.edges.get(fqn, ())):
            self._remove_edge(fqn, callee)
        self.edges.pop(fqn, None)
        self.nodes.discard(fqn)
        self.in_degree.pop(fqn, None)

    def _remove_edge(self, caller: str, callee: str) -> None:
        succ = self.edges.get(caller)
        if succ and callee in succ:
            succ.discard(callee)
            self.edge_info.pop((caller, callee), None)
            self.in_degree[callee] -= 1
            if self.in_degree[callee] == 0:
                del self.in_degree[callee]
                if callee not in self.edges and is_internal(callee):
                    self.nodes.discard(callee)

    # ---- 查询 ----
    def successors(self, node: str) -> Set[str]:
        return self.edges.get(node, set())

    def has_edge(self, caller: str, callee: str) -> bool:
        return callee in self.edges.get(caller, ())

    def edge_certain(self, caller: str, callee: str) -> bool:
        info = self.edge_info.get((caller, callee))
        return bool(info and info.certain)

    def num_edges(self) -> int:
        return len(self.edge_info)

    def stats(self) -> Dict[str, int]:
        internal = sum(1 for n in self.nodes if is_internal(n))
        certain = sum(1 for i in self.edge_info.values() if i.certain)
        return {
            "nodes": len(self.nodes),
            "internal_functions": internal,
            "external_nodes": len(self.nodes) - internal,
            "edges": len(self.edge_info),
            "certain_edges": certain,
            "uncertain_edges": len(self.edge_info) - certain,
        }

    # ---- 构建 ----
    @classmethod
    def from_model(cls, model) -> "CallGraph":
        g = cls()
        for fqn in model.functions:
            g.nodes.add(fqn)
            g.in_degree.setdefault(fqn, 0)
        for call in model.calls:
            g.add_call(call.caller, call.target, call.certain, call.kind)
        return g

    @classmethod
    def from_source(cls, source: str) -> "CallGraph":
        from .parser import parse_module

        return cls.from_model(parse_module(source))
