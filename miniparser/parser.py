"""递归下降解析器，支持两种模式：

- 严格模式（recover=False）：遇第一个语法错误即抛出 ParseError，对应旧行为。
- 容错模式（recover=True，默认）：错误恢复后继续解析，返回部分语法树
  与全部错误列表。

恢复策略（两级）：

1. 短语级恢复（补齐）：语句末尾缺少 ';'，而当前 token 是 '}'、EOF 或下一条
   语句的起始 token 时，视为补上一个虚拟分号——只记录错误，不消耗任何
   token，语句本身完整保留（标记 recovered=True），实现“失败后立即恢复”。

2. 恐慌模式恢复（跳过）：其余语法错误记录后，丢弃 token 直到同步点
   （';' 消耗掉；'}' 与 EOF 保留给外层结构），产生一个 kind="error" 的
   占位节点（recovered=True），props["skipped"] 记录被跳过的内容。
   若未跳过任何 token 且当前 token 不是同步点，则强制消耗一个 token，
   保证解析必然推进、不会死循环。

不确定性标记：kind="error" 的节点本身是恢复产物；任何在构建过程中发生过
恢复的语句节点也会被置 recovered=True，调用方可通过
ParseResult.uncertain_regions() 取得全部不确定子树。

不假成功：只要存在任何错误（词法或语法），ParseResult.incomplete 即为
True，与完整解析（incomplete=False）严格区分。
"""
from dataclasses import dataclass, field

from .lexer import tokenize
from .tokens import ErrorInfo, Pos, Token

SYNC_TOKENS = ("SEMI", "RBRACE", "EOF")

_EXPR_START_TYPES = ("NUMBER", "STRING", "IDENT", "LPAREN")
_STMT_START_TYPES = ("LET", "PRINT", "IF", "WHILE", "LBRACE") + _EXPR_START_TYPES


class ParseError(Exception):
    def __init__(self, pos, expected, actual):
        super().__init__(f"{pos}: 期望 {expected}，实际 {actual}")
        self.pos = pos
        self.expected = expected
        self.actual = actual


@dataclass
class Node:
    kind: str
    pos: Pos
    recovered: bool = False
    props: dict = field(default_factory=dict)


def walk(node):
    """先序遍历整棵语法树。"""
    yield node
    for value in node.props.values():
        if isinstance(value, Node):
            yield from walk(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    yield from walk(item)


@dataclass
class ParseResult:
    tree: Node
    errors: list

    @property
    def ok(self):
        """完整解析（无任何错误）时为 True。"""
        return not self.errors

    @property
    def incomplete(self):
        """存在未处理/被恢复的内容时为 True，绝不与完整解析混淆。"""
        return bool(self.errors)

    def uncertain_regions(self):
        """返回所有被标记为恢复产物的子树。"""
        return [n for n in walk(self.tree) if n.recovered]


def _describe(tok):
    if tok.type == "EOF":
        return "文件结尾"
    return f"{tok.value!r}"


class Parser:
    def __init__(self, tokens, errors, recover=True):
        self.tokens = tokens
        self.index = 0
        self.errors = errors
        self.recover = recover

    @property
    def cur(self):
        return self.tokens[self.index]

    def advance(self):
        tok = self.tokens[self.index]
        if tok.type != "EOF":
            self.index += 1
        return tok

    def _record(self, err):
        self.errors.append(ErrorInfo(err.pos, err.expected, err.actual))

    def _expect(self, type_, what, value=None):
        tok = self.cur
        if tok.type == type_ and (value is None or tok.value == value):
            return self.advance()
        raise ParseError(tok.pos, what, _describe(tok))

    def _expect_op(self, value):
        return self._expect("OP", repr(value), value)

    def _starts_statement(self):
        tok = self.cur
        return tok.type in _STMT_START_TYPES or (
            tok.type == "OP" and tok.value in ("-", "!")
        )

    def _expect_semi(self):
        if self.cur.type == "SEMI":
            self.advance()
            return
        err = ParseError(self.cur.pos, "';'", _describe(self.cur))
        if self.recover and (
            self.cur.type in ("RBRACE", "EOF") or self._starts_statement()
        ):
            # 短语级恢复：补一个虚拟分号，不消耗 token。
            self._record(err)
            return
        raise err

    # ---- 程序与语句 ----

    def parse_program(self):
        start = self.cur.pos
        body = []
        while self.cur.type != "EOF":
            body.append(self.parse_statement())
        return Node("program", start, props={"body": body})

    def parse_statement(self):
        if not self.recover:
            return self._parse_statement()
        before = len(self.errors)
        try:
            node = self._parse_statement()
        except ParseError as err:
            self._record(err)
            return self._panic_recover(err.pos)
        if len(self.errors) > before:
            # 该语句构建过程中发生过恢复（如补分号），标记为不确定。
            node.recovered = True
        return node

    def _parse_statement(self):
        tok = self.cur
        if tok.type == "LET":
            return self._let_stmt()
        if tok.type == "PRINT":
            return self._print_stmt()
        if tok.type == "IF":
            return self._if_stmt()
        if tok.type == "WHILE":
            return self._while_stmt()
        if tok.type == "LBRACE":
            return self._block()
        expr = self._expression()
        self._expect_semi()
        return Node("expr_stmt", expr.pos, props={"expr": expr})

    def _panic_recover(self, pos):
        skipped = []
        while self.cur.type not in SYNC_TOKENS:
            skipped.append(self.advance())
        if self.cur.type == "SEMI":
            self.advance()
        elif not skipped and self.cur.type != "EOF":
            # 未跳过任何 token 且不在同步点上（如顶层孤立的 '}'），
            # 强制消耗一个 token 保证推进。
            skipped.append(self.advance())
        return Node("error", pos, recovered=True,
                    props={"skipped": [t.value for t in skipped]})

    def _let_stmt(self):
        start = self.advance()
        name = self._expect("IDENT", "标识符")
        self._expect_op("=")
        value = self._expression()
        self._expect_semi()
        return Node("let", start.pos, props={"name": name.value, "value": value})

    def _print_stmt(self):
        start = self.advance()
        value = self._expression()
        self._expect_semi()
        return Node("print", start.pos, props={"value": value})

    def _if_stmt(self):
        start = self.advance()
        self._expect("LPAREN", "'('")
        cond = self._expression()
        self._expect("RPAREN", "')'")
        then = self.parse_statement()
        otherwise = None
        if self.cur.type == "ELSE":
            self.advance()
            otherwise = self.parse_statement()
        return Node("if", start.pos,
                    props={"cond": cond, "then": then, "else": otherwise})

    def _while_stmt(self):
        start = self.advance()
        self._expect("LPAREN", "'('")
        cond = self._expression()
        self._expect("RPAREN", "')'")
        body = self.parse_statement()
        return Node("while", start.pos, props={"cond": cond, "body": body})

    def _block(self):
        start = self.advance()
        body = []
        while self.cur.type not in ("RBRACE", "EOF"):
            body.append(self.parse_statement())
        self._expect("RBRACE", "'}'")
        return Node("block", start.pos, props={"body": body})

    # ---- 表达式（优先级递增） ----

    def _expression(self):
        return self._binary(self._comparison, ("==", "!="))

    def _comparison(self):
        return self._binary(self._additive, ("<", ">", "<=", ">="))

    def _additive(self):
        return self._binary(self._multiplicative, ("+", "-"))

    def _multiplicative(self):
        return self._binary(self._unary, ("*", "/"))

    def _binary(self, sub, ops):
        left = sub()
        while self.cur.type == "OP" and self.cur.value in ops:
            op = self.advance()
            right = sub()
            left = Node("binary", left.pos,
                        props={"op": op.value, "left": left, "right": right})
        return left

    def _unary(self):
        tok = self.cur
        if tok.type == "OP" and tok.value in ("-", "!"):
            self.advance()
            operand = self._unary()
            return Node("unary", tok.pos,
                        props={"op": tok.value, "operand": operand})
        return self._primary()

    def _primary(self):
        tok = self.cur
        if tok.type == "NUMBER":
            self.advance()
            return Node("number", tok.pos, props={"value": tok.value})
        if tok.type == "STRING":
            self.advance()
            return Node("string", tok.pos, props={"value": tok.value})
        if tok.type == "IDENT":
            self.advance()
            return Node("name", tok.pos, props={"name": tok.value})
        if tok.type == "LPAREN":
            self.advance()
            inner = self._expression()
            self._expect("RPAREN", "')'")
            return inner
        raise ParseError(tok.pos, "表达式", _describe(tok))


def parse(source, recover=True):
    """解析源码，返回 ParseResult。

    recover=True（默认）：错误恢复，返回部分语法树与全部错误。
    recover=False：严格模式，遇第一个错误抛出 ParseError（旧行为）。
    """
    tokens, lex_errors = tokenize(source)
    if not recover and lex_errors:
        first = lex_errors[0]
        raise ParseError(first.pos, first.expected, first.actual)
    errors = list(lex_errors)
    tree = Parser(tokens, errors, recover=recover).parse_program()
    # 按位置排序，保证输出稳定、与解析顺序无关。
    errors.sort(key=lambda e: (e.pos.offset, e.expected, e.actual))
    return ParseResult(tree=tree, errors=errors)


def parse_strict(source):
    """严格模式入口：完整解析成功则返回语法树，否则抛出 ParseError。"""
    return parse(source, recover=False).tree
