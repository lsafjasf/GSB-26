"""查询语法解析器（手写递归下降，仅标准库）。

语法（EBNF，关键字大小写不敏感，字段名大小写敏感）：

    query      := "SELECT" select_list "WHERE" conditions
                  [ "GROUP" "BY" ident ] [ "ORDER" "BY" ident ( "ASC" | "DESC" ) ]
                  [ "LIMIT" integer ] [ ";" ]
    select_list:= "*" | "COUNT(*)"
    conditions := condition ( "AND" condition )*
    condition  := ident op value
    op         := "=" | "!=" | "<" | "<=" | ">" | ">="
    value      := number | string | "true" | "false" | "null"
    string     := 单引号或双引号包围，支持 \\ \' \" \n \t 转义

语义约定：
- 内置时间字段为 ts（数值，秒级时间戳），每条记录必须存在。
- 条件之间仅支持 AND 合取。
- SELECT * 返回完整记录；SELECT COUNT(*) 返回计数，
  配合 GROUP BY 字段 做分组计数聚合。
- ORDER BY 仅允许按 ts 排序；LIMIT 配合 ORDER BY ts DESC 时
  引擎按块从新到旧扫描并提前终止。
- 缺失字段：任何比较均为假（!= 也不例外）。
- 跨类型比较（如 数值 与 字符串）恒为假，不抛异常。
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


class ParseError(ValueError):
    pass


@dataclass
class Condition:
    field: str
    op: str
    value: Any


@dataclass
class Query:
    select: str  # "*" 或 "count"
    conditions: List[Condition]
    group_by: Optional[str] = None
    order_by: Optional[str] = None
    order_desc: bool = False
    limit: Optional[int] = None


_KEYWORDS = {
    "select", "where", "and", "group", "by", "order", "asc", "desc",
    "limit", "true", "false", "null",
}

_OPS = ("!=", "<=", ">=", "=", "<", ">")


@dataclass
class _Tok:
    kind: str  # "ident" | "number" | "string" | "op" | "star" | "lparen" | "rparen" | "semi" | "eof"
    value: Any
    pos: int


def _tokenize(text: str) -> List[_Tok]:
    toks: List[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_."):
                j += 1
            word = text[i:j]
            toks.append(_Tok("ident", word, i))
            i = j
            continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and (text[i + 1].isdigit() or text[i + 1] == ".")):
            j = i + 1
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            num = text[i:j]
            value = float(num) if ("." in num) else int(num)
            toks.append(_Tok("number", value, i))
            i = j
            continue
        if ch in "'\"":
            j = i + 1
            buf = []
            while j < n and text[j] != ch:
                if text[j] == "\\" and j + 1 < n:
                    esc = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}.get(esc, esc))
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise ParseError(f"unterminated string at position {i}")
            toks.append(_Tok("string", "".join(buf), i))
            i = j + 1
            continue
        two = text[i:i + 2]
        if two in ("!=", "<=", ">="):
            toks.append(_Tok("op", two, i))
            i += 2
            continue
        if ch in "=<>":
            toks.append(_Tok("op", ch, i))
            i += 1
            continue
        if ch == "*":
            toks.append(_Tok("star", ch, i))
            i += 1
            continue
        if ch == "(":
            toks.append(_Tok("lparen", ch, i))
            i += 1
            continue
        if ch == ")":
            toks.append(_Tok("rparen", ch, i))
            i += 1
            continue
        if ch == ";":
            toks.append(_Tok("semi", ch, i))
            i += 1
            continue
        raise ParseError(f"unexpected character {ch!r} at position {i}")
    toks.append(_Tok("eof", None, n))
    return toks


class _Parser:
    def __init__(self, toks: List[_Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> _Tok:
        return self.toks[self.i]

    def next(self) -> _Tok:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def error(self, msg: str) -> ParseError:
        return ParseError(f"{msg} (at position {self.peek().pos})")

    def expect_keyword(self, word: str) -> None:
        tok = self.peek()
        if tok.kind == "ident" and tok.value.lower() == word:
            self.next()
            return
        raise self.error(f"expected {word.upper()}")

    def at_keyword(self, word: str) -> bool:
        tok = self.peek()
        return tok.kind == "ident" and tok.value.lower() == word

    def expect_ident(self) -> str:
        tok = self.peek()
        if tok.kind == "ident":
            self.next()
            return tok.value
        raise self.error("expected identifier")

    def parse(self) -> Query:
        self.expect_keyword("select")
        select = self._parse_select()
        self.expect_keyword("where")
        conditions = self._parse_conditions()
        group_by = None
        order_by = None
        order_desc = False
        limit = None
        if self.at_keyword("group"):
            self.next()
            self.expect_keyword("by")
            group_by = self.expect_ident()
        if self.at_keyword("order"):
            self.next()
            self.expect_keyword("by")
            order_by = self.expect_ident()
            if self.at_keyword("desc"):
                self.next()
                order_desc = True
            elif self.at_keyword("asc"):
                self.next()
            else:
                raise self.error("expected ASC or DESC after ORDER BY field")
        if self.at_keyword("limit"):
            self.next()
            tok = self.peek()
            if tok.kind != "number" or not isinstance(tok.value, int) or tok.value < 0:
                raise self.error("LIMIT requires a non-negative integer")
            self.next()
            limit = tok.value
        if self.peek().kind == "semi":
            self.next()
        if self.peek().kind != "eof":
            raise self.error("unexpected trailing input")
        return Query(
            select=select,
            conditions=conditions,
            group_by=group_by,
            order_by=order_by,
            order_desc=order_desc,
            limit=limit,
        )

    def _parse_select(self) -> str:
        if self.peek().kind == "star":
            self.next()
            return "*"
        if self.at_keyword("count"):
            self.next()
            if self.peek().kind != "lparen":
                raise self.error("expected ( after COUNT")
            self.next()
            if self.peek().kind != "star":
                raise self.error("only COUNT(*) is supported")
            self.next()
            if self.peek().kind != "rparen":
                raise self.error("expected ) after COUNT(*")
            self.next()
            return "count"
        raise self.error("expected * or COUNT(*) after SELECT")

    def _parse_conditions(self) -> List[Condition]:
        conditions = [self._parse_condition()]
        while self.at_keyword("and"):
            self.next()
            conditions.append(self._parse_condition())
        return conditions

    def _parse_condition(self) -> Condition:
        field = self.expect_ident()
        if field.lower() in _KEYWORDS:
            raise self.error(f"{field!r} is a reserved keyword, not a field name")
        tok = self.peek()
        if tok.kind != "op":
            raise self.error("expected comparison operator")
        self.next()
        value = self._parse_value()
        return Condition(field=field, op=tok.value, value=value)

    def _parse_value(self) -> Any:
        tok = self.peek()
        if tok.kind in ("number", "string"):
            self.next()
            return tok.value
        if tok.kind == "ident":
            low = tok.value.lower()
            if low == "true":
                self.next()
                return True
            if low == "false":
                self.next()
                return False
            if low == "null":
                self.next()
                return None
        raise self.error("expected a value (number, string, true, false, null)")


def parse(text: str) -> Query:
    """把查询文本解析为 Query AST，语法错误抛 ParseError。"""
    return _Parser(_tokenize(text)).parse()
