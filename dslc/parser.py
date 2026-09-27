"""配置 DSL 的词法分析、递归下降语法分析，产出带位置信息的 AST。

DSL 概览（详见 README.md）：

    param retries: int = 3
    param mode: enum(fast, slow)

    step fetch {
        needs: prev;
        args: { url: "http://x", retries: $retries }
    }

    step route {
        branch on $mode {
            fast: deploy_fast;
            slow: deploy_slow, notify;
            _: fallback;
        }
    }
"""

from dataclasses import dataclass, field
from typing import Optional

from .errors import CompileError

PUNCT = set("{}:,()[]=$;")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}


@dataclass(frozen=True)
class Token:
    kind: str            # "IDENT" | "STRING" | "NUMBER" | "PUNCT" | "EOF"
    value: object
    line: int
    col: int


def lex(text):
    tokens = []
    i, line, col = 0, 1, 1
    length = len(text)

    def advance(n=1):
        nonlocal i, col
        for _ in range(n):
            if i < length and text[i] == "\n":
                line_n = line + 1
                col_n = 1
            else:
                line_n, col_n = line, col + 1
            i += 1
        return line_n, col_n

    while i < length:
        c = text[i]
        if c == "\n":
            line, col = line + 1, 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            col += 1
            continue
        if c == "#":
            while i < length and text[i] != "\n":
                i += 1
                col += 1
            continue
        start_line, start_col = line, col
        if c == '"':
            i += 1
            col += 1
            buf = []
            while i < length and text[i] != '"':
                ch = text[i]
                if ch == "\n":
                    raise CompileError("unterminated string literal", start_line, start_col)
                if ch == "\\":
                    if i + 1 >= length:
                        raise CompileError("bad escape in string", line, col)
                    esc = text[i + 1]
                    if esc not in _ESCAPES:
                        raise CompileError(f"unknown escape '\\{esc}'", line, col)
                    buf.append(_ESCAPES[esc])
                    i += 2
                    col += 2
                else:
                    buf.append(ch)
                    i += 1
                    col += 1
            if i >= length:
                raise CompileError("unterminated string literal", start_line, start_col)
            i += 1
            col += 1
            tokens.append(Token("STRING", "".join(buf), start_line, start_col))
            continue
        if c.isdigit() or (c == "-" and i + 1 < length and text[i + 1].isdigit()):
            j = i + 1
            while j < length and (text[j].isdigit() or text[j] == "."):
                j += 1
            raw = text[i:j]
            try:
                number = float(raw) if "." in raw else int(raw)
            except ValueError:
                raise CompileError(f"invalid number {raw!r}", start_line, start_col)
            line, col = advance(j - i)
            tokens.append(Token("NUMBER", number, start_line, start_col))
            continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < length and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            line, col = advance(j - i)
            tokens.append(Token("IDENT", word, start_line, start_col))
            continue
        if c in PUNCT:
            i += 1
            col += 1
            tokens.append(Token("PUNCT", c, start_line, start_col))
            continue
        raise CompileError(f"unexpected character {c!r}", start_line, start_col)

    tokens.append(Token("EOF", None, line, col))
    return tokens


@dataclass
class ParamDecl:
    name: str
    ptype: str                       # str | int | float | bool | enum
    enum_values: Optional[tuple]
    has_default: bool
    default: object
    line: int
    col: int


@dataclass
class Value:
    kind: str                        # "lit" | "ref"
    value: object
    line: int
    col: int


@dataclass
class BranchCase:
    label: str                       # true | false | enum 值 | "_"
    members: list = field(default_factory=list)   # list[str]
    line: int = 0
    col: int = 0


@dataclass
class BranchDecl:
    cond: str                        # 参数名
    cases: list = field(default_factory=list)     # list[BranchCase]
    line: int = 0
    col: int = 0


@dataclass
class StepDecl:
    name: str
    needs: list = field(default_factory=list)     # list[tuple[str,int,int]]
    args: dict = field(default_factory=dict)      # name -> Value
    branch: Optional[BranchDecl] = None
    index: int = 0
    line: int = 0
    col: int = 0


class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def _tok(self):
        return self.toks[self.i]

    def eof(self):
        return self._tok().kind == "EOF"

    def accept(self, kind, value=None):
        tok = self._tok()
        if tok.kind == kind and (value is None or tok.value == value):
            self.i += 1
            return tok
        return None

    def expect(self, kind, value=None):
        tok = self._tok()
        if tok.kind != kind or (value is not None and tok.value != value):
            want = repr(value) if value is not None else kind
            raise CompileError(f"expected {want}, got {tok.value!r}", tok.line, tok.col)
        self.i += 1
        return tok

    def parse_param(self, kw):
        name = self.expect("IDENT")
        self.expect("PUNCT", ":")
        type_tok = self.expect("IDENT")
        enum_values = None
        if type_tok.value in ("str", "int", "float", "bool"):
            ptype = type_tok.value
        elif type_tok.value == "enum":
            self.expect("PUNCT", "(")
            values = [self.expect("IDENT").value]
            while self.accept("PUNCT", ","):
                values.append(self.expect("IDENT").value)
            self.expect("PUNCT", ")")
            ptype = "enum"
            enum_values = tuple(values)
        else:
            raise CompileError(
                f"unknown type {type_tok.value!r}", type_tok.line, type_tok.col
            )
        has_default, default = False, None
        if self.accept("PUNCT", "="):
            tok = self._tok()
            has_default = True
            default = self.parse_bare_literal()
        return ParamDecl(
            name.value, ptype, enum_values, has_default, default, kw.line, kw.col
        )

    def parse_bare_literal(self):
        tok = self._tok()
        if tok.kind == "STRING" or tok.kind == "NUMBER":
            self.i += 1
            return tok.value
        if tok.kind == "IDENT" and tok.value in ("true", "false"):
            self.i += 1
            return tok.value == "true"
        if tok.kind == "IDENT":
            self.i += 1
            return tok.value
        raise CompileError(
            f"expected literal, got {tok.value!r}", tok.line, tok.col
        )

    def parse_ident_list(self):
        result = [self.expect("IDENT")]
        while self.accept("PUNCT", ","):
            result.append(self.expect("IDENT"))
        return [(t.value, t.line, t.col) for t in result]

    def parse_step(self, kw, index):
        name = self.expect("IDENT")
        self.expect("PUNCT", "{")
        needs, args, branch = [], {}, None
        while not self.accept("PUNCT", "}"):
            section = self.expect("IDENT")
            if section.value == "needs":
                self.expect("PUNCT", ":")
                needs = self.parse_ident_list()
                self.expect("PUNCT", ";")
            elif section.value == "args":
                self.expect("PUNCT", ":")
                args = self.parse_args()
            elif section.value == "branch":
                if branch is not None:
                    raise CompileError(
                        "duplicate branch section in step", section.line, section.col
                    )
                branch = self.parse_branch(section)
            else:
                raise CompileError(
                    f"unknown section {section.value!r}, expected needs/args/branch",
                    section.line,
                    section.col,
                )
        return StepDecl(name.value, needs, args, branch, index, kw.line, kw.col)

    def parse_args(self):
        self.expect("PUNCT", "{")
        args = {}
        while not self.accept("PUNCT", "}"):
            key = self.expect("IDENT")
            self.expect("PUNCT", ":")
            if key.value in args:
                raise CompileError(
                    f"duplicate argument {key.value!r}", key.line, key.col
                )
            args[key.value] = self.parse_value()
            if self.accept("PUNCT", ","):
                continue
            self.expect("PUNCT", "}")
            break
        return args

    def parse_value(self):
        tok = self._tok()
        if tok.kind in ("STRING", "NUMBER"):
            self.i += 1
            return Value("lit", tok.value, tok.line, tok.col)
        if tok.kind == "IDENT" and tok.value in ("true", "false"):
            self.i += 1
            return Value("lit", tok.value == "true", tok.line, tok.col)
        if tok.kind == "PUNCT" and tok.value == "$":
            self.i += 1
            ref = self.expect("IDENT")
            return Value("ref", ref.value, tok.line, tok.col)
        if tok.kind == "PUNCT" and tok.value == "[":
            self.i += 1
            items = []
            if not self.accept("PUNCT", "]"):
                items.append(self.parse_value())
                while self.accept("PUNCT", ","):
                    if self.accept("PUNCT", "]"):
                        break
                    items.append(self.parse_value())
                self.expect("PUNCT", "]")
            return Value("lit", items, tok.line, tok.col)
        raise CompileError(
            f"expected literal or $param, got {tok.value!r}", tok.line, tok.col
        )

    def parse_branch(self, kw):
        on = self.expect("IDENT")
        if on.value != "on":
            raise CompileError(f"expected 'on', got {on.value!r}", on.line, on.col)
        self.expect("PUNCT", "$")
        cond = self.expect("IDENT")
        self.expect("PUNCT", "{")
        cases = []
        while not self.accept("PUNCT", "}"):
            label = self.expect("IDENT")
            self.expect("PUNCT", ":")
            members = []
            if not self.accept("PUNCT", ";"):
                for member, _, _ in self.parse_ident_list():
                    members.append(member)
                self.expect("PUNCT", ";")
            if any(c.label == label.value for c in cases):
                raise CompileError(
                    f"duplicate case {label.value!r}", label.line, label.col
                )
            cases.append(BranchCase(label.value, members, label.line, label.col))
        return BranchDecl(cond.value, cases, kw.line, kw.col)


def parse(text):
    """把 DSL 文本解析为 (params, steps) 两个声明列表。"""
    parser = _Parser(lex(text))
    params, steps = [], []
    while not parser.eof():
        keyword = parser.expect("IDENT")
        if keyword.value == "param":
            params.append(parser.parse_param(keyword))
        elif keyword.value == "step":
            steps.append(parser.parse_step(keyword, len(steps)))
        else:
            raise CompileError(
                f"expected 'param' or 'step', got {keyword.value!r}",
                keyword.line,
                keyword.col,
            )
    return params, steps
