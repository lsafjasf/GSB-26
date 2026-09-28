"""词法分析：把源码切成 token 流。

词法层容错：遇到无法识别的字符时记录一条 lex 错误并跳过该字符继续扫描，
保证单个坏字符不会中断整个文件的分析。
"""
import re

from .tokens import ErrorInfo, Pos, Token

KEYWORDS = {
    "let": "LET",
    "print": "PRINT",
    "if": "IF",
    "else": "ELSE",
    "while": "WHILE",
}

TOKEN_RULES = [
    ("NUMBER", r"\d+(?:\.\d+)?"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("STRING", r'"[^"\n]*"'),
    ("OP", r"==|!=|<=|>=|[-+*/=<>!]"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("SEMI", r";"),
    ("COMMA", r","),
]

_MASTER = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_RULES))
_WS = re.compile(r"\s+")


def tokenize(source):
    """返回 (tokens, errors)。tokens 末尾恒有 EOF；errors 为词法错误列表。"""
    tokens = []
    errors = []
    offset = 0
    line = 1
    col = 1

    def advance(text):
        nonlocal offset, line, col
        for ch in text:
            offset += 1
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1

    while offset < len(source):
        m = _WS.match(source, offset)
        if m:
            advance(m.group())
            continue
        m = _MASTER.match(source, offset)
        if m is None:
            errors.append(ErrorInfo(Pos(offset, line, col), "合法字符",
                                    repr(source[offset]), phase="lex"))
            advance(source[offset])
            continue
        kind = m.lastgroup
        value = m.group()
        pos = Pos(offset, line, col)
        if kind == "IDENT" and value in KEYWORDS:
            kind = KEYWORDS[value]
        tokens.append(Token(kind, value, pos))
        advance(value)

    tokens.append(Token("EOF", "", Pos(offset, line, col)))
    return tokens, errors
