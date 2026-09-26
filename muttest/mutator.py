"""Mutation operators and mutant generation (standard library only).

Four operator classes are implemented:

1. boundary_value        -- numeric constants n -> n+1 / n-1, and
                            comparison boundary shift (< -> <=, > -> >=, ...)
2. conditional_negation  -- comparison negation (< -> >=, == -> !=, ...),
                            boolean operator swap (and <-> or), removal of `not`
3. return_replacement    -- `return expr` -> `return None`, `return True` <-> `return False`
4. statement_deletion    -- a statement is replaced by `pass`

Only *mutable* positions are considered: docstrings, imports, function/class
definitions, `pass`, global/nonlocal declarations are never mutated, and only
single-operator comparisons are touched.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

BOUNDARY_CMP = {"Lt": "LtE", "LtE": "Lt", "Gt": "GtE", "GtE": "Gt"}
NEGATE_CMP = {
    "Lt": "GtE", "GtE": "Lt", "Gt": "LtE", "LtE": "Gt",
    "Eq": "NotEq", "NotEq": "Eq",
}
NEGATE_BOOL = {"And": "Or", "Or": "And"}

OPERATORS = ("boundary_value", "conditional_negation",
             "return_replacement", "statement_deletion")


class MutantNotApplicable(Exception):
    pass


@dataclass
class Mutant:
    kind: str
    node_type: str
    lineno: int
    col: int
    end_lineno: int
    end_col: int
    detail: str
    payload: tuple

    @property
    def id(self) -> str:
        digest = hashlib.sha1(self.detail.encode()).hexdigest()[:6]
        return f"{self.kind}:{self.lineno}:{self.col}:{digest}"

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "lineno": self.lineno,
                "col": self.col, "detail": self.detail}


class _Collector(ast.NodeVisitor):
    """Collect expression-level mutation sites."""

    def __init__(self):
        self.mutants: list[Mutant] = []

    def _add(self, kind, node, detail, payload):
        self.mutants.append(Mutant(
            kind, type(node).__name__, node.lineno, node.col_offset,
            node.end_lineno, node.end_col_offset, detail, payload))

    def visit_Constant(self, node):
        # bool is a subclass of int; booleans are handled by return_replacement
        if isinstance(node.value, bool) or node.value is None:
            return
        if isinstance(node.value, (int, float)):
            self._add("boundary_value", node,
                      f"{node.value!r} -> {node.value + 1!r}",
                      ("const", node.value + 1))
            self._add("boundary_value", node,
                      f"{node.value!r} -> {node.value - 1!r}",
                      ("const", node.value - 1))

    def visit_Compare(self, node):
        if len(node.ops) == 1:
            name = type(node.ops[0]).__name__
            if name in BOUNDARY_CMP:
                self._add("boundary_value", node,
                          f"{name} -> {BOUNDARY_CMP[name]}",
                          ("cmpop", BOUNDARY_CMP[name]))
            if name in NEGATE_CMP:
                self._add("conditional_negation", node,
                          f"{name} -> {NEGATE_CMP[name]}",
                          ("cmpop", NEGATE_CMP[name]))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        name = type(node.op).__name__
        if name in NEGATE_BOOL:
            self._add("conditional_negation", node,
                      f"{name} -> {NEGATE_BOOL[name]}",
                      ("boolop", NEGATE_BOOL[name]))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self._add("conditional_negation", node, "remove `not`",
                      ("remove_not",))
        self.generic_visit(node)

    def visit_Return(self, node):
        if node.value is None:
            return
        if (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, bool)):
            flipped = not node.value.value
            self._add("return_replacement", node,
                      f"return {node.value.value} -> return {flipped}",
                      ("return_const", flipped))
        else:
            self._add("return_replacement", node, "return expr -> return None",
                      ("return_none",))
        self.generic_visit(node)


# Statements that are never deleted (structure / non-mutable positions).
_STATEMENT_SKIP = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                   ast.Import, ast.ImportFrom, ast.Pass,
                   ast.Global, ast.Nonlocal)


def _is_docstring(stmt) -> bool:
    return (isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def _collect_deletions(tree, mutants):
    for parent in ast.walk(tree):
        for field_name in ("body", "orelse", "finalbody"):
            body = getattr(parent, field_name, None)
            if not isinstance(body, list):
                continue
            for index, stmt in enumerate(body):
                if isinstance(stmt, _STATEMENT_SKIP):
                    continue
                if index == 0 and field_name == "body" and _is_docstring(stmt):
                    continue
                mutants.append(Mutant(
                    "statement_deletion", type(stmt).__name__,
                    stmt.lineno, stmt.col_offset,
                    stmt.end_lineno, stmt.end_col_offset,
                    f"delete {type(stmt).__name__}", ("delete",)))


def collect_mutants(source: str) -> list[Mutant]:
    tree = ast.parse(source)
    collector = _Collector()
    collector.visit(tree)
    _collect_deletions(tree, collector.mutants)
    collector.mutants.sort(key=lambda m: (m.lineno, m.col, m.kind, m.detail))
    return collector.mutants


class _Applier(ast.NodeTransformer):
    def __init__(self, mutant: Mutant):
        self.mutant = mutant
        self.applied = False

    def _matches(self, node) -> bool:
        m = self.mutant
        return (type(node).__name__ == m.node_type
                and getattr(node, "lineno", None) == m.lineno
                and getattr(node, "col_offset", None) == m.col
                and getattr(node, "end_lineno", None) == m.end_lineno
                and getattr(node, "end_col_offset", None) == m.end_col)

    def visit(self, node):
        if not self.applied and self._matches(node):
            self.applied = True
            return self._replace(node)
        return super().visit(node)

    def _replace(self, node):
        op = self.mutant.payload[0]
        if op == "delete":
            return ast.copy_location(ast.Pass(), node)
        if op == "const":
            return ast.copy_location(ast.Constant(value=self.mutant.payload[1]), node)
        if op == "cmpop":
            new = ast.Compare(left=node.left,
                              ops=[getattr(ast, self.mutant.payload[1])()],
                              comparators=node.comparators)
            return ast.copy_location(new, node)
        if op == "boolop":
            new = ast.BoolOp(op=getattr(ast, self.mutant.payload[1])(),
                             values=node.values)
            return ast.copy_location(new, node)
        if op == "remove_not":
            return node.operand
        if op == "return_none":
            return ast.copy_location(ast.Return(value=ast.Constant(value=None)), node)
        if op == "return_const":
            new = ast.Return(value=ast.Constant(value=self.mutant.payload[1]))
            return ast.copy_location(new, node)
        raise ValueError(f"unknown payload: {op}")


def apply_mutant(source: str, mutant: Mutant) -> str:
    """Return the mutated source code for exactly one mutant."""
    tree = ast.parse(source)
    applier = _Applier(mutant)
    tree = applier.visit(tree)
    if not applier.applied:
        raise MutantNotApplicable(mutant.id)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)
