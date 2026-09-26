"""AST node definitions for the DSL."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypeSpec:
    kind: str              # 'str' | 'int' | 'bool' | 'enum' | 'enumlit'
    values: tuple = ()     # enum values when kind == 'enum'

    def __str__(self):
        if self.kind == "enum":
            return "enum(" + ", ".join(self.values) + ")"
        if self.kind == "enumlit":
            return "enum literal"
        return self.kind


STR = TypeSpec("str")
INT = TypeSpec("int")
BOOL = TypeSpec("bool")
ENUMLIT = TypeSpec("enumlit")   # unresolved enum literal, unified during type check


class Expr:
    line = 0
    col = 0


@dataclass
class Lit(Expr):
    value: object
    type: TypeSpec
    line: int
    col: int


@dataclass
class Unresolved(Expr):
    """A bare identifier: a parameter reference or an enum literal.

    The validator resolves it and sets ``is_ref`` accordingly.
    """
    name: str
    line: int
    col: int
    is_ref: bool = False


@dataclass
class Bin(Expr):
    op: str          # == != < <= > >= and or
    left: Expr
    right: Expr
    line: int
    col: int


@dataclass
class Not(Expr):
    operand: Expr
    line: int
    col: int


@dataclass
class Param:
    name: str
    type: TypeSpec
    default: Expr        # Lit / Unresolved / None
    line: int
    col: int


@dataclass
class Case:
    label: Expr          # Lit or Unresolved (must be a literal, not a ref)
    target: object       # (name, line, col) or None for 'skip'
    line: int
    col: int
    value: object = None  # normalized label value, filled by the validator


@dataclass
class Branch:
    on: Expr
    cases: list          # list[Case]
    else_target: object  # (name, line, col) or None ('else -> skip')
    line: int
    col: int
    has_else: bool = False


@dataclass
class Step:
    name: str
    needs: list          # list[(name, line, col)]
    when: Expr           # or None
    args: list           # list[(key, value_node, line, col)]
    run: str             # or None
    branch: Branch       # or None
    line: int
    col: int


@dataclass
class Config:
    params: list         # list[Param]
    steps: list          # list[Step]
