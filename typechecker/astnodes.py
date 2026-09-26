"""AST 节点定义。每个节点都携带源码位置 Loc，用于错误定位。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass(frozen=True)
class Loc:
    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.line}:{self.col}"


class Node:
    loc: Loc


@dataclass
class IntLit(Node):
    value: int
    loc: Loc


@dataclass
class BoolLit(Node):
    value: bool
    loc: Loc


@dataclass
class StrLit(Node):
    value: str
    loc: Loc


@dataclass
class Var(Node):
    name: str
    loc: Loc


@dataclass
class Lam(Node):
    param: str
    ann: Optional["object"]  # 可选参数类型标注（types.Type）
    body: Node
    loc: Loc


@dataclass
class App(Node):
    func: Node
    arg: Node
    loc: Loc


@dataclass
class Let(Node):
    name: str
    ann: Optional["object"]
    value: Node
    body: Node
    loc: Loc


@dataclass
class If(Node):
    cond: Node
    then: Node
    otherwise: Node
    loc: Loc


@dataclass
class Ann(Node):
    expr: Node
    ann: "object"
    loc: Loc


@dataclass
class ListLit(Node):
    items: List[Node]
    loc: Loc


@dataclass
class Binding:
    name: str
    ann: Optional["object"]
    expr: Node
    loc: Loc


@dataclass
class Def:
    name: str
    ann: Optional["object"]
    expr: Node
    loc: Loc


@dataclass
class DefRec:
    bindings: List[Binding]
    loc: Loc


TopLevel = Union[Def, DefRec, Node]
Program = List[TopLevel]


def count_nodes(program: Program) -> int:
    """统计程序 AST 节点总数（用于性能报告的节点规模）。"""
    total = 0

    def visit(node: Node) -> None:
        nonlocal total
        total += 1
        if isinstance(node, Lam):
            visit(node.body)
        elif isinstance(node, App):
            visit(node.func)
            visit(node.arg)
        elif isinstance(node, Let):
            visit(node.value)
            visit(node.body)
        elif isinstance(node, If):
            visit(node.cond)
            visit(node.then)
            visit(node.otherwise)
        elif isinstance(node, Ann):
            visit(node.expr)
        elif isinstance(node, ListLit):
            for item in node.items:
                visit(item)

    for item in program:
        if isinstance(item, Def):
            total += 1
            visit(item.expr)
        elif isinstance(item, DefRec):
            total += 1
            for binding in item.bindings:
                total += 1
                visit(binding.expr)
        else:
            visit(item)
    return total
