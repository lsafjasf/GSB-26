"""Single-template validation: scoping, type merging, signature extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from .errors import Diagnostic, Position
from .parser import Each, If, Node, Placeholder, Text, parse


@dataclass
class ParamInfo:
    """One entry of a template's parameter signature."""

    name: str
    type: str = "any"
    optional: bool = False
    first_pos: Optional[Position] = None
    order: int = 0  # index of first occurrence (document order)
    _seen_optional: Optional[bool] = field(default=None, repr=False)

    def __str__(self) -> str:
        suffix = "?" if self.optional else ""
        return f"{self.name}:{self.type}{suffix}"


@dataclass
class ValidationResult:
    ok: bool
    errors: List[Diagnostic] = field(default_factory=list)
    warnings: List[Diagnostic] = field(default_factory=list)
    signature: Dict[str, ParamInfo] = field(default_factory=dict)  # insertion-ordered
    loop_var_types: Dict[str, str] = field(default_factory=dict)
    ast: List[Node] = field(default_factory=list)

    @property
    def order(self) -> List[str]:
        """Parameter names in first-occurrence order."""
        return [n for n, _ in sorted(self.signature.items(), key=lambda kv: kv[1].order)]


def merge_type(a: str, b: str) -> Optional[str]:
    """Merge two type annotations; None if they conflict."""
    if a == b:
        return a
    if a == "any":
        return b
    if b == "any":
        return a
    return None


class _Scope:
    """One lexical scope introduced by an {#each} section."""

    def __init__(self, var: str, pos: Position):
        self.var = var
        self.pos = pos
        self.type = "any"  # inferred from usages inside the body


def validate(source: Union[str, List[Node]]) -> ValidationResult:
    """Validate one template and extract its parameter signature.

    Checks performed (all reported with exact positions):

    * ``TYPE_CONFLICT``         - same variable annotated with incompatible types
    * ``OUT_OF_SCOPE``          - loop variable referenced outside its section
    * ``OPTIONAL_INCONSISTENT`` - warning: mixed ``{x}`` / ``{x?}`` occurrences
    """
    ast = parse(source) if isinstance(source, str) else source
    errors: List[Diagnostic] = []
    warnings: List[Diagnostic] = []
    signature: Dict[str, ParamInfo] = {}
    loop_var_types: Dict[str, str] = {}
    loop_var_names = _collect_loop_vars(ast)
    counter = [0]

    def get_param(name: str, pos: Position) -> ParamInfo:
        info = signature.get(name)
        if info is None:
            info = ParamInfo(name=name, first_pos=pos, order=counter[0])
            counter[0] += 1
            signature[name] = info
        return info

    def merge_into(holder, new_type, pos, owner_desc):
        old = holder.type
        merged = merge_type(old, new_type)
        if merged is None:
            errors.append(
                Diagnostic(
                    "TYPE_CONFLICT",
                    f"{owner_desc} used with incompatible types "
                    f"'{old}' and '{new_type}'",
                    pos,
                )
            )
        else:
            holder.type = merged

    def resolve(name, pos, scopes, implied_type, why):
        """Resolve `name`; merge the implied type into param or loop var."""
        for scope in reversed(scopes):
            if scope.var == name:
                merge_into(scope, implied_type, pos, f"loop variable '{name}'")
                return
        if name in loop_var_names:
            errors.append(
                Diagnostic(
                    "OUT_OF_SCOPE",
                    f"'{name}' is a loop variable introduced by an "
                    f"{{#each ... as {name}}} section and cannot be "
                    f"referenced here ({why})",
                    pos,
                )
            )
            return
        merge_into(get_param(name, pos), implied_type, pos, f"parameter '{name}'")

    def merge_optional(info: ParamInfo, node: Placeholder) -> None:
        if info._seen_optional is None:
            info._seen_optional = node.optional
            info.optional = node.optional
        elif info._seen_optional != node.optional:
            warnings.append(
                Diagnostic(
                    "OPTIONAL_INCONSISTENT",
                    f"parameter '{info.name}' is marked optional in one "
                    f"occurrence and required in another; treating it as required",
                    node.pos,
                )
            )
            info.optional = False
            info._seen_optional = False

    def walk(nodes: List[Node], scopes: List[_Scope]) -> None:
        for node in nodes:
            if isinstance(node, Text):
                continue
            if isinstance(node, Placeholder):
                in_scope = any(s.var == node.name for s in scopes)
                resolve(node.name, node.pos, scopes, node.type, "placeholder")
                if not in_scope and node.name not in loop_var_names:
                    info = signature.get(node.name)
                    if info is not None:
                        merge_optional(info, node)
            elif isinstance(node, If):
                resolve(node.cond, node.pos, scopes, "bool", "{#if} condition")
                walk(node.body, scopes)
            elif isinstance(node, Each):
                resolve(node.source, node.pos, scopes, "list", "{#each} source")
                scope = _Scope(node.var, node.pos)
                walk(node.body, scopes + [scope])
                prev = loop_var_types.get(node.var)
                if prev is None:
                    loop_var_types[node.var] = scope.type
                else:
                    merged = merge_type(prev, scope.type)
                    loop_var_types[node.var] = merged if merged else prev
            else:  # pragma: no cover - defensive
                raise TypeError(f"unknown node {node!r}")

    walk(ast, [])
    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        signature=signature,
        loop_var_types=loop_var_types,
        ast=ast,
    )


def _collect_loop_vars(nodes: List[Node]) -> set:
    names = set()
    for node in nodes:
        if isinstance(node, Each):
            names.add(node.var)
            names |= _collect_loop_vars(node.body)
        elif isinstance(node, If):
            names |= _collect_loop_vars(node.body)
    return names
