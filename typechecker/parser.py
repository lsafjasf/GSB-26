"""DSL 的 S-表达式解析器，输出带位置信息的 AST。

语法概览：
  顶层      := (def 名 (: 类型)? 表达式) | (defrec (名 (: 类型)? 表达式)+) | 表达式
  表达式    := 整数 | "字符串" | true | false | nil | 变量
             | (fn 参数 主体)                       参数 := 名 | (名 : 类型)
             | (let 名 (: 类型)? 值 主体)
             | (if 条件  then  else)
             | (the 类型 表达式)                    类型标注
             | (list 表达式*)                       列表字面量
             | (函数 参数*)                         调用（多参数自动柯里化）
  类型      := Int | Bool | Str | 'a | (List 类型) | (-> 类型 类型+)
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .astnodes import (Ann, App, Binding, BoolLit, Def, DefRec, If, IntLit,
                       Lam, Let, ListLit, Loc, Node, Program, StrLit, Var)
from .types import TCon, TFun, TList, TVar, Type


class ParseError(Exception):
    def __init__(self, msg: str, loc: Optional[Loc]):
        self.loc = loc
        super().__init__(f"{loc}: {msg}" if loc else msg)


@dataclass
class Tok:
    kind: str  # lpar | rpar | colon | int | str | sym
    text: str
    loc: Loc


TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>;[^\n]*)
  | (?P<lpar>\()
  | (?P<rpar>\))
  | (?P<colon>:)
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<int>-?\d+)
  | (?P<sym>[^\s():";]+)
""", re.VERBOSE)

SPECIALS = {"def", "defrec", "fn", "let", "if", "the", "list"}


def tokenize(src: str) -> List[Tok]:
    line_starts = [0]
    for m in re.finditer("\n", src):
        line_starts.append(m.end())

    def loc_of(offset: int) -> Loc:
        line = bisect.bisect_right(line_starts, offset)
        col = offset - line_starts[line - 1] + 1
        return Loc(line, col)

    toks: List[Tok] = []
    pos = 0
    for m in TOKEN_RE.finditer(src):
        if m.start() != pos:
            raise ParseError(f"非法字符 {src[pos]!r}", loc_of(pos))
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        toks.append(Tok(kind, m.group(), loc_of(m.start())))
    if pos != len(src):
        raise ParseError(f"非法字符 {src[pos]!r}", loc_of(pos))
    return toks


class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self, k: int = 0) -> Optional[Tok]:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def next(self) -> Tok:
        tok = self.peek()
        if tok is None:
            raise ParseError("意外的输入结束", None)
        self.i += 1
        return tok

    def expect(self, kind: str, what: str) -> Tok:
        tok = self.next()
        if tok.kind != kind:
            raise ParseError(f"期望{what}，得到 {tok.text!r}", tok.loc)
        return tok

    def at_end(self) -> bool:
        return self.i >= len(self.toks)

    # ---------- 类型 ----------

    def parse_type(self, varmap: Dict[str, TVar]) -> Type:
        tok = self.next()
        if tok.kind == "sym":
            text = tok.text
            if text in ("Int", "Bool", "Str"):
                return TCon(text, origin=(tok.loc, f"类型标注 {text}"))
            if text.startswith("'"):
                if text not in varmap:
                    varmap[text] = TVar(hint=text, origin=(tok.loc, f"泛型占位 {text}"))
                return varmap[text]
            raise ParseError(f"未知类型 {text!r}", tok.loc)
        if tok.kind == "lpar":
            head = self.expect("sym", "类型构造子")
            if head.text == "List":
                elem = self.parse_type(varmap)
                self.expect("rpar", "')'")
                return TList(elem, origin=(tok.loc, "类型标注 (List ...)"))
            if head.text == "->":
                parts: List[Type] = []
                while self.peek() is not None and self.peek().kind != "rpar":
                    parts.append(self.parse_type(varmap))
                self.expect("rpar", "')'")
                if len(parts) < 2:
                    raise ParseError("(-> ...) 至少需要两个类型", tok.loc)
                cur = parts[-1]
                for part in reversed(parts[:-1]):
                    cur = TFun(part, cur, origin=(tok.loc, "类型标注 (-> ...)"))
                return cur
            raise ParseError(f"未知类型构造子 {head.text!r}", head.loc)
        raise ParseError("期望类型", tok.loc if tok else None)

    def try_ann(self) -> Optional[Type]:
        """尝试解析可选的 (: 类型) 标注。"""
        if self.peek() and self.peek().kind == "lpar" and \
           self.peek(1) and self.peek(1).kind == "colon":
            self.next()  # (
            self.next()  # :
            ann = self.parse_type({})
            self.expect("rpar", "')'")
            return ann
        return None

    # ---------- 表达式 ----------

    def parse_toplevel(self):
        if self.peek() and self.peek().kind == "lpar" and \
           self.peek(1) and self.peek(1).kind == "sym" and \
           self.peek(1).text in ("def", "defrec"):
            start = self.next()  # (
            kw = self.next().text
            if kw == "def":
                name = self.expect("sym", "定义名").text
                ann = self.try_ann()
                expr = self.parse_expr()
                self.expect("rpar", "')'")
                return Def(name, ann, expr, start.loc)
            bindings: List[Binding] = []
            while self.peek() and self.peek().kind == "lpar":
                bstart = self.next()  # (
                bname = self.expect("sym", "绑定名").text
                bann = self.try_ann()
                bexpr = self.parse_expr()
                self.expect("rpar", "')'")
                bindings.append(Binding(bname, bann, bexpr, bstart.loc))
            self.expect("rpar", "')'")
            if not bindings:
                raise ParseError("defrec 至少需要一个绑定", start.loc)
            return DefRec(bindings, start.loc)
        return self.parse_expr()

    def parse_expr(self) -> Node:
        tok = self.next()
        if tok.kind == "int":
            return IntLit(int(tok.text), tok.loc)
        if tok.kind == "str":
            return StrLit(bytes(tok.text[1:-1], "utf-8").decode("unicode_escape"), tok.loc)
        if tok.kind == "sym":
            if tok.text == "true":
                return BoolLit(True, tok.loc)
            if tok.text == "false":
                return BoolLit(False, tok.loc)
            if tok.text == "nil":
                return ListLit([], tok.loc)
            return Var(tok.text, tok.loc)
        if tok.kind != "lpar":
            raise ParseError(f"期望表达式，得到 {tok.text!r}", tok.loc)

        head = self.peek()
        if head is None:
            raise ParseError("空表达式", tok.loc)
        if head.kind == "sym" and head.text in SPECIALS:
            kw = self.next().text
            if kw in ("def", "defrec"):
                raise ParseError(f"{kw} 只能出现在顶层", head.loc)
            if kw == "fn":
                param_tok = self.next()
                if param_tok.kind == "sym":
                    param, ann = param_tok.text, None
                elif param_tok.kind == "lpar":
                    param = self.expect("sym", "参数名").text
                    self.expect("colon", "':'")
                    ann = self.parse_type({})
                    self.expect("rpar", "')'")
                else:
                    raise ParseError("fn 期望参数", param_tok.loc)
                body = self.parse_expr()
                self.expect("rpar", "')'")
                return Lam(param, ann, body, tok.loc)
            if kw == "let":
                name = self.expect("sym", "绑定名").text
                ann = self.try_ann()
                value = self.parse_expr()
                body = self.parse_expr()
                self.expect("rpar", "')'")
                return Let(name, ann, value, body, tok.loc)
            if kw == "if":
                cond = self.parse_expr()
                then = self.parse_expr()
                otherwise = self.parse_expr()
                self.expect("rpar", "')'")
                return If(cond, then, otherwise, tok.loc)
            if kw == "the":
                ann = self.parse_type({})
                expr = self.parse_expr()
                self.expect("rpar", "')'")
                return Ann(expr, ann, tok.loc)
            if kw == "list":
                items: List[Node] = []
                while self.peek() is not None and self.peek().kind != "rpar":
                    items.append(self.parse_expr())
                self.expect("rpar", "')'")
                return ListLit(items, tok.loc)

        # 函数调用（支持多参数，自动柯里化）
        func = self.parse_expr()
        node = func
        while self.peek() is not None and self.peek().kind != "rpar":
            arg = self.parse_expr()
            node = App(node, arg, tok.loc)
        self.expect("rpar", "')'")
        return node


def parse_program(src: str) -> Program:
    parser = Parser(tokenize(src))
    items: Program = []
    while not parser.at_end():
        items.append(parser.parse_toplevel())
    return items
