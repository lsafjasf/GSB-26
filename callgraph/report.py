"""环检测报告格式化。"""

from __future__ import annotations

from typing import Dict, List, Set

from .cycles import Cycle
from .graph import CallGraph


def format_report(graph: CallGraph, cycles: List[Cycle]) -> str:
    lines: List[str] = []
    st = graph.stats()
    lines.append("=== 调用图统计 ===")
    lines.append(
        "内部函数 {internal_functions} | 外部/未知节点 {external_nodes} | "
        "边 {edges}（确定 {certain_edges}，不确定 {uncertain_edges}）".format(**st)
    )
    lines.append("")
    lines.append("=== 环检测报告 ===")
    if not cycles:
        lines.append("未发现环。")
        return "\n".join(lines)

    self_rec = [c for c in cycles if c.kind == "self-recursion"]
    mutual = [c for c in cycles if c.kind == "mutual-recursion"]
    involved: Set[str] = set()
    for c in cycles:
        involved |= c.functions
    lines.append(
        f"共 {len(cycles)} 个环：直接递归 {len(self_rec)} 个，相互递归 {len(mutual)} 个；"
        f"涉及函数 {len(involved)} 个。"
    )
    lines.append("")

    def fmt_cycle(c: Cycle) -> str:
        parts = []
        for i, node in enumerate(c.path):
            nxt = c.path[(i + 1) % len(c.path)]
            mark = "" if c.edges[i] and graph.edge_certain(*c.edges[i]) else "~?"
            parts.append(node + (mark if mark else ""))
        loop = " -> ".join(parts) + " -> " + c.path[0]
        conf = "" if c.confidence == "certain" else "，含不确定边（potential）"
        return f"  长度 {c.length} | {loop}{conf}"

    if self_rec:
        lines.append(f"[直接递归 / 自环]（{len(self_rec)} 个）")
        for c in self_rec:
            lines.append(fmt_cycle(c))
        lines.append("")
    if mutual:
        lines.append(f"[相互递归]（{len(mutual)} 个）")
        for c in mutual:
            lines.append(fmt_cycle(c))
        lines.append("")
    lines.append("涉及函数: " + ", ".join(sorted(involved)))
    return "\n".join(lines)
