"""查询语法解析器（仅标准库）。

语法（EBNF）：

    query        := filter_stage ("|" stage)*
    filter_stage := condition ("AND" condition)*        -- 可为空，表示匹配全部
    condition    := IDENT OP value
    OP           := "=" | "!=" | "<" | "<=" | ">" | ">="
    value        := NUMBER | STRING | IDENT             -- 未加引号的标识符按字符串处理
    stage        := "count" "by" IDENT                  -- 分组计数聚合
                  | "top" NUMBER                        -- 按 ts 倒序取前 N 条（提前终止）

示例：
    ts>=1700000000 AND ts<1700003600 AND level="ERROR"
    service="api" AND latency>=100 | count by level
    status=599 | top 20
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<op>>=|<=|!=|=|>|<)
      | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<pipe>\|)
    )
    """,
    re.VERBOSE,
)

_OPS = {"=", "!=", "<", "<=", ">", ">="}


@dataclass(frozen=True)
class Cond:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Query:
    conds: Tuple[Cond, ...] = ()
    count_by: Optional[str] = None   # 分组计数字段
    top: Optional[int] = None        # 按 ts 倒序取前 N 条
    source: str = ""


class ParseError(ValueError):
    pass


def _tokenize(text: str) -> List[Tuple[str, Any]]:
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(f"无法解析的位置 {pos}: {text[pos:pos+20]!r}")
        pos = m.end()
        kind = m.lastgroup
        raw = m.group(kind)
        if kind == "string":
            tokens.append(("value", raw[1:-1]))
        elif kind == "number":
            tokens.append(("value", float(raw) if "." in raw else int(raw)))
        elif kind == "ident":
            tokens.append(("ident", raw))
        else:
            tokens.append((kind, raw))
    return tokens


def parse(text: str) -> Query:
    tokens = _tokenize(text)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else (None, None)

    def expect(kind, value=None):
        nonlocal pos
        k, v = peek()
        if k != kind or (value is not None and v != value):
            raise ParseError(f"期望 {value or kind}，实际得到 {v!r}")
        pos += 1
        return v

    conds: List[Cond] = []
    count_by: Optional[str] = None
    top: Optional[int] = None

    # 过滤阶段：condition (AND condition)*，可为空
    def at_condition():
        k, v = peek()
        return (k == "ident" and v != "AND"
                and pos + 1 < len(tokens) and tokens[pos + 1][0] == "op")

    def parse_condition():
        field_name = expect("ident")
        op = expect("op")
        if op not in _OPS:
            raise ParseError(f"不支持的运算符: {op}")
        vk, vv = peek()
        if vk == "value":
            nonlocal_pos = peek()
            expect("value")
            return Cond(field_name, op, vv)
        if vk == "ident":
            expect("ident")
            return Cond(field_name, op, vv)  # 裸标识符按字符串处理
        raise ParseError(f"条件右值非法: {vv!r}")

    if at_condition():
        conds.append(parse_condition())
        while True:
            k, v = peek()
            if k == "ident" and v == "AND":
                pos += 1
                if not at_condition():
                    raise ParseError("AND 之后必须是条件")
                conds.append(parse_condition())
            else:
                break

    # 管道阶段
    while True:
        k, v = peek()
        if k != "pipe":
            break
        pos += 1
        k, v = peek()
        if k == "ident" and v == "count":
            pos += 1
            expect("ident", "by")
            if count_by is not None:
                raise ParseError("只能有一个 count by 阶段")
            count_by = expect("ident")
        elif k == "ident" and v == "top":
            pos += 1
            vk, vv = peek()
            if vk != "value" or not isinstance(vv, int) or vv <= 0:
                raise ParseError("top 后必须跟正整数")
            pos += 1
            if top is not None:
                raise ParseError("只能有一个 top 阶段")
            top = vv
        else:
            raise ParseError(f"未知的管道阶段: {v!r}")

    if pos != len(tokens):
        raise ParseError(f"查询末尾存在多余内容: {tokens[pos:]!r}")
    if count_by is not None and top is not None:
        raise ParseError("count by 与 top 不能同时使用")

    return Query(conds=tuple(conds), count_by=count_by, top=top, source=text)
