#!/usr/bin/env python3
"""Reference lexer: an independent, hand-written scanner (no `re` module).

Implements exactly the same config semantics as lexer.Lexer so the two can
be differentially tested against each other. Shares only the Token/LexError
data types and the position-advance helper; all matching logic is separate.
"""
from __future__ import annotations

from lexer import Token, LexError, advance, load_config


def _is_alpha_class(ch):
    # Mirrors regex [^\W\d_]: word character, not a decimal, not '_'.
    return ch.isalnum() and not ch.isdecimal()


def _predicate(name):
    if name == "alpha":
        return _is_alpha_class
    if name == "digit":
        return lambda ch: "0" <= ch <= "9"
    if name == "underscore":
        return lambda ch: ch == "_"
    raise ValueError(f"unknown identifier class: {name!r}")


def _match_whitespace(spec, text, pos, n):
    chars = spec["chars"]
    i = pos
    while i < n and text[i] in chars:
        i += 1
    return (i - pos, None) if i > pos else None


def _match_line_comment(spec, text, pos, n):
    prefix = spec["prefix"]
    if not text.startswith(prefix, pos):
        return None
    i = pos + len(prefix)
    while i < n and text[i] != "\n":
        i += 1
    return (i - pos, None)


def _match_block_comment(spec, text, pos, n):
    start, end = spec["start"], spec["end"]
    if not text.startswith(start, pos):
        return None
    if not spec.get("nested", False):
        idx = text.find(end, pos + len(start))
        if idx < 0:
            return (n - pos, "unterminated block comment")
        return (idx + len(end) - pos, None)
    depth = 1
    i = pos + len(start)
    while i < n:
        if text.startswith(start, i):
            depth += 1
            i += len(start)
        elif text.startswith(end, i):
            depth -= 1
            i += len(end)
            if depth == 0:
                return (i - pos, None)
        else:
            i += 1
    return (n - pos, "unterminated block comment")


def _match_string(spec, text, pos, n):
    quote = spec["quote"]
    escape = spec.get("escape")
    multiline = bool(spec.get("multiline", False))
    if text[pos] != quote:
        return None
    i = pos + 1
    while i < n:
        ch = text[i]
        if escape and ch == escape:
            if i + 1 >= n:
                break  # dangling escape at EOF: stop before the escape char
            if text[i + 1] == "\n" and not multiline:
                break  # escape cannot swallow a newline here
            i += 2
            continue
        if ch == quote:
            return (i + 1 - pos, None)
        if ch == "\n" and not multiline:
            break
        i += 1
    return (i - pos, "unterminated string literal")


def _match_number(spec, text, pos, n):
    us = bool(spec.get("allow_underscore", False))

    def is_dec(c):
        return "0" <= c <= "9"

    def is_hex(c):
        return c in "0123456789abcdefABCDEF"

    def consume_digits(i, isdig):
        # consumes: digit ( '_'? digit )*   -- text[i] must be a digit
        i += 1
        while i < n:
            if isdig(text[i]):
                i += 1
            elif us and text[i] == "_" and i + 1 < n and isdig(text[i + 1]):
                i += 2
            else:
                break
        return i

    if not is_dec(text[pos]):
        return None
    if (spec.get("allow_hex", False) and text[pos] == "0"
            and pos + 2 < n and text[pos + 1] in "xX"
            and is_hex(text[pos + 2])):
        return (consume_digits(pos + 2, is_hex) - pos, None)
    i = consume_digits(pos, is_dec)
    if (spec.get("allow_float", False) and i < n and text[i] == "."
            and i + 1 < n and is_dec(text[i + 1])):
        i = consume_digits(i + 1, is_dec)
    if spec.get("allow_exponent", False) and i < n and text[i] in "eE":
        j = i + 1
        if j < n and text[j] in "+-":
            j += 1
        if j < n and is_dec(text[j]):
            i = consume_digits(j, is_dec)
    return (i - pos, None)


def _match_identifier(spec, text, pos, n):
    start_preds = spec["_start_preds"]
    cont_preds = spec["_cont_preds"]
    if not any(p(text[pos]) for p in start_preds):
        return None
    i = pos + 1
    while i < n and any(p(text[i]) for p in cont_preds):
        i += 1
    return (i - pos, None)


def _match_literal(spec, text, pos, n):
    for lit in spec["_literals"]:
        if text.startswith(lit, pos):
            return (len(lit), None)
    return None


_KIND_MATCHERS = {
    "whitespace": _match_whitespace,
    "line_comment": _match_line_comment,
    "block_comment": _match_block_comment,
    "string": _match_string,
    "number": _match_number,
    "identifier": _match_identifier,
    "keywords": _match_literal,
    "operators": _match_literal,
}


class RefLexer:
    """Hand-written scanner interpreting the same rule config."""

    def __init__(self, config):
        config = load_config(config)
        self.rules = []
        for raw in config["rules"]:
            spec = dict(raw)
            kind = spec["kind"]
            if kind not in _KIND_MATCHERS:
                raise ValueError(f"unknown rule kind: {kind!r}")
            if kind == "identifier":
                spec["_start_preds"] = [_predicate(x) for x in spec["start"]]
                spec["_cont_preds"] = [_predicate(x) for x in spec["continue"]]
            if kind in ("keywords", "operators"):
                key = "words" if kind == "keywords" else "ops"
                spec["_literals"] = sorted(spec[key], key=len, reverse=True)
            self.rules.append((spec, _KIND_MATCHERS[kind]))

    def tokenize(self, text: str):
        tokens, errors = [], []
        pos, line, col, n = 0, 1, 1, len(text)
        while pos < n:
            best_len, best_spec, best_err = 0, None, None
            for spec, matcher in self.rules:
                r = matcher(spec, text, pos, n)
                if r is not None and r[0] > best_len:
                    best_len, best_spec, best_err = r[0], spec, r[1]
            if best_spec is None:
                errors.append(LexError(f"illegal character {text[pos]!r}",
                                       line, col))
                line, col = advance(line, col, text[pos])
                pos += 1
                continue
            start_line, start_col = line, col
            segment = text[pos:pos + best_len]
            line, col = advance(line, col, segment)
            pos += best_len
            if best_err:
                errors.append(LexError(best_err, start_line, start_col))
            if not best_spec.get("skip", False):
                tokens.append(Token(best_spec["name"], segment,
                                    start_line, start_col, line, col))
        return tokens, errors
