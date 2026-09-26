"""Parser for the tplcheck template language.

Syntax
------
* Text is literal.  ``{{`` and ``}}`` escape literal braces.
* Placeholder: ``{name}``, ``{name:type}``, ``{name:type?}``, ``{name?}``
  where ``type`` is one of: any, str, int, float, bool, date, datetime, list.
  A trailing ``?`` marks the argument optional (may be missing / None).
* Conditional section: ``{#if name} ... {/if}``
* Loop section:        ``{#each items as item} ... {/each}``

The parser produces a small AST defined below.  Every node carries the
position of the token that created it so diagnostics can be precise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .errors import Position, TemplateSyntaxError

TYPES = frozenset({"any", "str", "int", "float", "bool", "date", "datetime", "list"})

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


@dataclass
class Text:
    value: str
    pos: Position


@dataclass
class Placeholder:
    name: str
    type: str  # one of TYPES; "any" when unannotated
    optional: bool
    pos: Position


@dataclass
class If:
    cond: str
    body: List["Node"]
    pos: Position


@dataclass
class Each:
    source: str  # name of the list parameter
    var: str  # loop variable, scoped to body
    body: List["Node"]
    pos: Position


Node = Union[Text, Placeholder, If, Each]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass
class _Token:
    kind: str  # "text" | "ph" | "if_open" | "each_open" | "close"
    pos: Position
    escaped: bool = False
    # text
    value: str = ""
    # placeholder
    name: str = ""
    type: str = "any"
    optional: bool = False
    # each
    var: str = ""
    # close
    tag: str = ""


_TAG_RE = re.compile(r"\{\{|\}\}|\{[^{}]*\}")


def _positions(src: str):
    line_starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            line_starts.append(i + 1)

    def pos_at(offset: int) -> Position:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return Position(line=lo + 1, column=offset - line_starts[lo] + 1, offset=offset)

    return pos_at


def _tokenize(src: str) -> List[_Token]:
    pos_at = _positions(src)
    tokens: List[_Token] = []
    cursor = 0
    for m in _TAG_RE.finditer(src):
        if m.start() > cursor:
            tokens.append(_Token("text", pos_at(cursor), value=src[cursor : m.start()]))
        pos = pos_at(m.start())
        if m.group(0) == "{{":
            tokens.append(_Token("text", pos, escaped=True, value="{"))
        elif m.group(0) == "}}":
            tokens.append(_Token("text", pos, escaped=True, value="}"))
        else:
            inner = m.group(0)[1:-1]
            tokens.append(_parse_tag(inner.strip(), pos))
        cursor = m.end()
    if cursor < len(src):
        tokens.append(_Token("text", pos_at(cursor), value=src[cursor:]))
    # A lone "{" or "}" that the regex did not match is a syntax error.
    for tok in tokens:
        if tok.kind == "text" and not tok.escaped:
            for i, ch in enumerate(tok.value):
                if ch in "{}":
                    raise TemplateSyntaxError(
                        f"unescaped '{ch}'; use '{{{{' or '}}}}' for a literal brace",
                        _positions(src)(tok.pos.offset + i),
                    )
    return tokens


def _parse_tag(inner: str, pos: Position) -> _Token:
    if inner.startswith("#if "):
        name = inner[4:].strip()
        _check_name(name, pos)
        return _Token("if_open", pos, name=name)
    if inner.startswith("#each "):
        m = re.fullmatch(r"#each\s+(\S+)\s+as\s+(\S+)", inner)
        if not m:
            raise TemplateSyntaxError(
                "loop section must look like '{#each items as item}'", pos
            )
        _check_name(m.group(1), pos)
        _check_name(m.group(2), pos)
        return _Token("each_open", pos, name=m.group(1), var=m.group(2))
    if inner.startswith("/"):
        tag = inner[1:].strip()
        if tag not in ("if", "each"):
            raise TemplateSyntaxError(f"unknown closing tag '{{/{tag}}}'", pos)
        return _Token("close", pos, tag=tag)
    if inner.startswith("#"):
        raise TemplateSyntaxError(f"unknown section tag '{{{inner}}}'", pos)
    # placeholder: name[:type][?]
    m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?::([A-Za-z]+))?(\?)?", inner)
    if not m:
        raise TemplateSyntaxError(f"malformed placeholder '{{{inner}}}'", pos)
    name, typ, opt = m.group(1), m.group(2) or "any", m.group(3)
    if typ not in TYPES:
        raise TemplateSyntaxError(
            f"unknown type '{typ}'; expected one of {sorted(TYPES)}", pos
        )
    return _Token("ph", pos, name=name, type=typ, optional=bool(opt))


def _check_name(name: str, pos: Position) -> None:
    if not _NAME_RE.fullmatch(name):
        raise TemplateSyntaxError(f"invalid identifier '{name}'", pos)


# ---------------------------------------------------------------------------
# Recursive-descent parse
# ---------------------------------------------------------------------------


def parse(src: str) -> List[Node]:
    """Parse a template string into a list of AST nodes.

    Raises :class:`TemplateSyntaxError` on malformed input.
    """
    tokens = _tokenize(src)
    nodes, index, closer = _parse_block(tokens, 0, expect_close=None)
    if closer is not None:  # pragma: no cover - defensive
        raise TemplateSyntaxError("unexpected closing tag", closer.pos)
    if index != len(tokens):  # pragma: no cover - defensive
        raise TemplateSyntaxError("unexpected trailing tokens")
    return nodes


def _parse_block(tokens, index, expect_close):
    """Parse tokens until a matching close tag (or EOF).

    Returns (nodes, next_index, close_token_or_None).
    """
    nodes: List[Node] = []
    while index < len(tokens):
        tok = tokens[index]
        if tok.kind == "text":
            nodes.append(Text(tok.value, tok.pos))
            index += 1
        elif tok.kind == "ph":
            nodes.append(Placeholder(tok.name, tok.type, tok.optional, tok.pos))
            index += 1
        elif tok.kind == "if_open":
            body, index, closer = _parse_block(tokens, index + 1, expect_close="if")
            if closer is None:
                raise TemplateSyntaxError("unclosed '{#if ...}' section", tok.pos)
            nodes.append(If(tok.name, body, tok.pos))
        elif tok.kind == "each_open":
            body, index, closer = _parse_block(tokens, index + 1, expect_close="each")
            if closer is None:
                raise TemplateSyntaxError("unclosed '{#each ...}' section", tok.pos)
            nodes.append(Each(tok.name, tok.var, body, tok.pos))
        elif tok.kind == "close":
            if expect_close is None:
                raise TemplateSyntaxError(
                    f"closing tag '{{/{tok.tag}}}' without matching section", tok.pos
                )
            if tok.tag != expect_close:
                raise TemplateSyntaxError(
                    f"expected '{{/{expect_close}}}' but found '{{/{tok.tag}}}'",
                    tok.pos,
                )
            return nodes, index + 1, tok
    return nodes, index, None
