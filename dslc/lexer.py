"""Tokenizer for the DSL. Produces tokens with 1-based line/col positions."""

from dataclasses import dataclass

from .errors import DSLError

KEYWORDS = {
    "param", "step", "needs", "when", "args", "run",
    "branch", "on", "case", "else", "skip",
    "and", "or", "not", "true", "false",
}

# Multi-character punctuation must come before single-character prefixes.
PUNCS = ["->", "==", "!=", "<=", ">=", "{", "}", "(", ")", ":", ",", "=", "<", ">"]

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


@dataclass
class Token:
    kind: str   # IDENT | INT | STRING | KW | PUNC | EOF
    value: object
    line: int
    col: int


def tokenize(text):
    tokens = []
    i, line, col = 0, 1, 1
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r":
            i += 1
            col += 1
            continue
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == '"':
            start_col = col
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise DSLError("unterminated string literal", line, start_col)
                ch = text[j]
                if ch == '"':
                    break
                if ch == "\n":
                    raise DSLError("newline in string literal", line, start_col)
                if ch == "\\":
                    j += 1
                    if j >= n:
                        raise DSLError("unterminated string literal", line, start_col)
                    buf.append(_ESCAPES.get(text[j], text[j]))
                    j += 1
                else:
                    buf.append(ch)
                    j += 1
            tokens.append(Token("STRING", "".join(buf), line, start_col))
            col += j - i + 1
            i = j + 1
            continue
        if c.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(Token("INT", int(text[i:j]), line, col))
            col += j - i
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            kind = "KW" if word in KEYWORDS else "IDENT"
            tokens.append(Token(kind, word, line, col))
            col += j - i
            i = j
            continue
        matched = False
        for p in PUNCS:
            if text.startswith(p, i):
                tokens.append(Token("PUNC", p, line, col))
                i += len(p)
                col += len(p)
                matched = True
                break
        if matched:
            continue
        raise DSLError("unexpected character {!r}".format(c), line, col)
    tokens.append(Token("EOF", None, line, col))
    return tokens
