"""Reference renderer used to differential-test the validator.

The renderer is intentionally strict: anything the validator rejects must
also fail here, and anything the validator accepts must render cleanly when
given arguments that match the extracted signature.
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Union

from .errors import RenderError
from .parser import Each, If, Node, Placeholder, Text, parse


def check_value(value, type_name: str, where: str) -> None:
    """Raise RenderError if `value` does not satisfy `type_name`."""
    ok = True
    if type_name == "any":
        ok = True
    elif type_name == "str":
        ok = isinstance(value, str)
    elif type_name == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif type_name == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif type_name == "bool":
        ok = isinstance(value, bool)
    elif type_name == "date":
        ok = isinstance(value, datetime.date)
    elif type_name == "datetime":
        ok = isinstance(value, datetime.datetime)
    elif type_name == "list":
        ok = isinstance(value, list)
    else:  # pragma: no cover - defensive
        raise RenderError(f"unknown type '{type_name}' at {where}")
    if not ok:
        raise RenderError(
            f"{where}: expected {type_name}, got "
            f"{type(value).__name__} ({value!r})"
        )


def format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def render(template: Union[str, List[Node]], args: Dict) -> str:
    """Render `template` with `args`. Raises RenderError on any problem."""
    ast = parse(template) if isinstance(template, str) else template
    out: List[str] = []
    _render_nodes(ast, [dict(args)], out)
    return "".join(out)


def _lookup(scopes: List[Dict], name: str, where: str):
    for scope in reversed(scopes):
        if name in scope:
            return scope[name], True
    return None, False


def _render_nodes(nodes: List[Node], scopes: List[Dict], out: List[str]) -> None:
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.value)
        elif isinstance(node, Placeholder):
            where = f"placeholder '{{{node.name}}}' at {node.pos}"
            value, found = _lookup(scopes, node.name, where)
            if not found:
                if node.optional:
                    out.append("")
                    continue
                raise RenderError(f"{where}: missing argument '{node.name}'")
            if value is None:
                if node.optional:
                    out.append("")
                    continue
                raise RenderError(f"{where}: argument '{node.name}' is None")
            check_value(value, node.type, where)
            out.append(format_value(value))
        elif isinstance(node, If):
            where = f"{{#if {node.cond}}} at {node.pos}"
            value, found = _lookup(scopes, node.cond, where)
            if not found:
                raise RenderError(f"{where}: missing argument '{node.cond}'")
            check_value(value, "bool", where)
            if value:
                _render_nodes(node.body, scopes, out)
        elif isinstance(node, Each):
            where = f"{{#each {node.source} as {node.var}}} at {node.pos}"
            value, found = _lookup(scopes, node.source, where)
            if not found:
                raise RenderError(f"{where}: missing argument '{node.source}'")
            check_value(value, "list", where)
            for item in value:
                scopes.append({node.var: item})
                try:
                    _render_nodes(node.body, scopes, out)
                finally:
                    scopes.pop()
        else:  # pragma: no cover - defensive
            raise RenderError(f"unknown node {node!r}")
