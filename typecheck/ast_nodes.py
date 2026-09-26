"""AST nodes. Every node carries a source position for diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pos:
    line: int = 0
    col: int = 0

    def __str__(self):
        return f"{self.line}:{self.col}"


@dataclass
class Node:
    pos: Pos = field(default_factory=Pos)


@dataclass
class IntLit(Node):
    value: int = 0


@dataclass
class BoolLit(Node):
    value: bool = False


@dataclass
class StrLit(Node):
    value: str = ""


@dataclass
class Var(Node):
    name: str = ""


@dataclass
class Lam(Node):
    param: str = ""
    body: Node = None
    ann: "str | None" = None


@dataclass
class Call(Node):
    fn: Node = None
    arg: Node = None


@dataclass
class Let(Node):
    name: str = ""
    value: Node = None
    body: Node = None
    ann: "str | None" = None


@dataclass
class Binding:
    name: str
    value: Node
    ann: "str | None" = None


@dataclass
class LetRec(Node):
    bindings: list = field(default_factory=list)  # list[Binding], one mutually recursive group
    body: Node = None


@dataclass
class If(Node):
    cond: Node = None
    then: Node = None
    otherwise: Node = None


@dataclass
class Program(Node):
    groups: list = field(default_factory=list)  # list[list[Binding]]: top-level recursive groups
    main: "Node | None" = None
