"""增量更新引擎。

改动少量函数时：
1. 只替换被改函数的出边（O(该函数调用数)）。
2. 计算受影响子图 R = 内部可达(f) ∪ 内部反向可达(f) —— 任何经过 f 的环都完整落在 R 内。
3. 仅在 R 上重跑环检测，替换缓存中涉及 f 的环；与 f 无关的环原样保留。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .cycles import Cycle, find_cycles
from .graph import CallGraph, is_internal
from .parser import ModuleModel, parse_function_source


@dataclass
class UpdateReport:
    function: str
    region_size: int = 0          # 受影响子图的节点数
    cycles_before: int = 0
    cycles_after: int = 0
    added: List[Cycle] = field(default_factory=list)
    removed: List[Cycle] = field(default_factory=list)


class IncrementalEngine:
    def __init__(self, graph: CallGraph, model: Optional[ModuleModel] = None):
        self.graph = graph
        self.model = model
        self.cycles: List[Cycle] = find_cycles(graph)
        self._index: Dict[str, Set[int]] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._index = {}
        for i, c in enumerate(self.cycles):
            for f in c.functions:
                self._index.setdefault(f, set()).add(i)

    # ---- 受影响子图 ----
    def affected_region(self, f: str) -> Set[str]:
        adj = self.graph.edges
        radj: Dict[str, Set[str]] = {}
        for u, succs in adj.items():
            for v in succs:
                if is_internal(v):
                    radj.setdefault(v, set()).add(u)

        def bfs(start: str, table) -> Set[str]:
            seen = {start}
            stack = [start]
            while stack:
                u = stack.pop()
                for v in table.get(u, ()):
                    if is_internal(v) and v not in seen:
                        seen.add(v)
                        stack.append(v)
            return seen

        return bfs(f, adj) | bfs(f, radj)

    # ---- 更新入口 ----
    def update_calls(self, f: str, calls: Iterable) -> UpdateReport:
        """用新的调用列表替换函数 f 的全部出边。calls 元素：CallSite 或
        (target, certain[, kind]) 元组。"""
        old_cycles = [c for c in self.cycles if f in c.functions]
        self.graph.replace_calls(f, calls)
        return self._refresh(f, old_cycles)

    def update_function_source(self, f: str, source: str) -> UpdateReport:
        """用新源码替换函数 f（需构造时传入 model）。"""
        if self.model is None:
            raise RuntimeError("update_function_source 需要 model")
        old_cycles = [c for c in self.cycles if f in c.functions]
        new_calls = parse_function_source(source, f, self.model)
        self.graph.replace_calls(f, new_calls)
        return self._refresh(f, old_cycles)

    def remove_function(self, f: str) -> UpdateReport:
        old_cycles = [c for c in self.cycles if f in c.functions]
        self.graph.remove_function(f)
        if self.model is not None:
            self.model.functions.pop(f, None)
        return self._refresh(f, old_cycles)

    # ---- 内部 ----
    def _refresh(self, f: str, old_cycles: List[Cycle]) -> UpdateReport:
        region = self.affected_region(f)
        new_involving = find_cycles(self.graph, restrict_to={f})
        old_set = {c.path for c in old_cycles}
        new_set = {c.path for c in new_involving}
        kept = [c for c in self.cycles if f not in c.functions]
        self.cycles = sorted(kept + new_involving, key=lambda c: (c.length, c.path))
        self._rebuild_index()
        return UpdateReport(
            function=f,
            region_size=len(region),
            cycles_before=len(old_cycles),
            cycles_after=len(new_involving),
            added=[c for c in new_involving if c.path not in old_set],
            removed=[c for c in old_cycles if c.path not in new_set],
        )
