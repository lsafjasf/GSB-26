#!/usr/bin/env python3
"""Configurable lexer library (main implementation, regex-backed engine).

Rules are declared in a JSON config. Tokenization follows two principles:
  1. Longest match wins across all rules at the current position.
  2. Ties are broken by rule priority (order of rules in the config).

Positions are 1-based (line, col). Token end position is exclusive, i.e. it
points one past the last character of the token. Errors never abort the scan:
illegal characters are reported and skipped, unterminated strings/comments
are reported and still emitted as tokens.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class Token:
    type: str
    text: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def key(self):
        return (self.type, self.text, self.start_line, self.start_col,
                self.end_line, self.end_col)


@dataclass
class LexError:
    message: str
    line: int
    col: int

    def key(self):
        return (self.message, self.line, self.col)


class ConfigError(ValueError):
    pass


def advance(line: int, col: int, segment: str) -> Tuple[int, int]:
    """Position after consuming `segment`; only '\\n' starts a new line."""
    newlines = segment.count("\n")
    if newlines:
        return line + newlines, len(segment) - segment.rfind("\n")
    return line, col + len(segment)


# Identifier character-class fragments shared by config semantics:
#   "alpha"      -> unicode word chars except decimals and '_'  ([^\W\d_])
#   "digit"      -> ASCII 0-9
#   "underscore" -> '_'
_CLASS_FRAGMENTS = {
    "alpha": r"[^\W\d_]",
    "digit": r"[0-9]",
    "underscore": r"_",
}

# Common fragment unions fused into a single character class (faster).
_FUSED_CLASSES = {
    frozenset(["alpha"]): r"[^\W\d_]",
    frozenset(["alpha", "underscore"]): r"[^\W\d]",
    frozenset(["alpha", "digit"]): r"(?:[^\W\d_]|[0-9])",
    frozenset(["alpha", "digit", "underscore"]): r"(?:[^\W\d_]|[0-9_])",
    frozenset(["digit"]): r"[0-9]",
    frozenset(["underscore"]): r"_",
    frozenset(["digit", "underscore"]): r"[0-9_]",
}


class Rule:
    """One compiled lexical rule. `match(text, pos)` returns
    (length, error_message_or_None) or None when the rule does not apply."""

    def __init__(self, spec: dict):
        self.name = spec["name"]
        self.kind = spec["kind"]
        self.skip = bool(spec.get("skip", False))
        self.spec = spec
        builder = getattr(self, "_build_" + self.kind, None)
        if builder is None:
            raise ConfigError(f"unknown rule kind: {self.kind!r}")
        builder()

    def match(self, text: str, pos: int):
        return self._matcher(text, pos)

    def could_start(self, ch: str) -> bool:
        """Cheap check: may this rule match a string starting with `ch`?
        Used to skip rules that cannot apply at the current position."""
        return self._first(ch)

    # ---- whitespace -----------------------------------------------------
    def _build_whitespace(self):
        chars = "".join(re.escape(c) for c in self.spec["chars"])
        self._regex = re.compile("[" + chars + "]+")
        self._matcher = self._match_regex
        ws = set(self.spec["chars"])
        self._first = lambda ch: ch in ws

    # ---- line comment ---------------------------------------------------
    def _build_line_comment(self):
        self._regex = re.compile(re.escape(self.spec["prefix"]) + r"[^\n]*")
        self._matcher = self._match_regex
        first = self.spec["prefix"][0]
        self._first = lambda ch: ch == first

    def _match_regex(self, text, pos):
        m = self._regex.match(text, pos)
        return (m.end() - pos, None) if m else None

    # ---- block comment (manual scan: regex cannot count nesting) --------
    def _build_block_comment(self):
        self._start = self.spec["start"]
        self._end = self.spec["end"]
        self._nested = bool(self.spec.get("nested", False))
        self._matcher = self._match_block_comment
        first = self._start[0]
        self._first = lambda ch: ch == first

    def _match_block_comment(self, text, pos):
        start, end = self._start, self._end
        if not text.startswith(start, pos):
            return None
        n = len(text)
        if not self._nested:
            idx = text.find(end, pos + len(start))
            if idx < 0:
                return (n - pos, "unterminated block comment")
            return (idx + len(end) - pos, None)
        depth = 1
        i = pos + len(start)
        ls, le = len(start), len(end)
        while i < n:
            if text.startswith(start, i):
                depth += 1
                i += ls
            elif text.startswith(end, i):
                depth -= 1
                i += le
                if depth == 0:
                    return (i - pos, None)
            else:
                i += 1
        return (n - pos, "unterminated block comment")

    # ---- string ----------------------------------------------------------
    def _build_string(self):
        quote = self.spec["quote"]
        escape = self.spec.get("escape")
        multiline = bool(self.spec.get("multiline", False))
        if len(quote) != 1:
            raise ConfigError("string quote must be a single character")
        excluded = quote + (escape or "") + ("" if multiline else "\n")
        cls = "[^" + re.escape(excluded) + "]"
        if escape:
            anyc = "(?s:.)" if multiline else "."
            body = "(?:" + re.escape(escape) + anyc + "|" + cls + ")"
        else:
            body = cls
        q = re.escape(quote)
        self._terminated = re.compile(q + body + "*" + q)
        self._open = re.compile(q + body + "*")
        self._matcher = self._match_string
        self._first = lambda ch: ch == quote

    def _match_string(self, text, pos):
        m = self._terminated.match(text, pos)
        if m:
            return (m.end() - pos, None)
        m = self._open.match(text, pos)
        if m:
            return (m.end() - pos, "unterminated string literal")
        return None

    # ---- number ------------------------------------------------------------
    def _build_number(self):
        us = bool(self.spec.get("allow_underscore", False))
        dec_digits = r"[0-9](?:_?[0-9])*" if us else r"[0-9]+"
        parts = []
        if self.spec.get("allow_hex", False):
            hex_digits = (r"[0-9A-Fa-f](?:_?[0-9A-Fa-f])*" if us
                          else r"[0-9A-Fa-f]+")
            parts.append(r"0[xX]" + hex_digits)
        dec = dec_digits
        if self.spec.get("allow_float", False):
            dec += r"(?:\." + dec_digits + r")?"
        if self.spec.get("allow_exponent", False):
            dec += r"(?:[eE][+-]?" + dec_digits + r")?"
        parts.append(dec)
        self._regex = re.compile("|".join(parts))
        self._matcher = self._match_regex
        self._first = lambda ch: "0" <= ch <= "9"

    # ---- identifier --------------------------------------------------------
    def _build_identifier(self):
        start_pat = self._class_pattern(self.spec["start"])
        self._regex = re.compile(
            start_pat + self._class_pattern(self.spec["continue"]) + "*")
        self._matcher = self._match_regex
        start_re = re.compile(start_pat)
        self._first = lambda ch: start_re.match(ch) is not None

    @staticmethod
    def _class_pattern(names):
        for nm in names:
            if nm not in _CLASS_FRAGMENTS:
                raise ConfigError(f"unknown identifier class: {nm!r}")
        key = frozenset(names)
        fused = _FUSED_CLASSES.get(key)
        if fused is not None:
            return fused
        parts, simple = [], ""
        for nm in names:
            frag = _CLASS_FRAGMENTS[nm]
            if frag.startswith("["):
                parts.append(frag)
            else:
                simple += re.escape(frag)
        if simple:
            parts.append("[" + simple + "]")
        return "(?:" + "|".join(parts) + ")"

    # ---- keywords / operators (literal prefixes) ---------------------------
    def _build_keywords(self):
        self._literals = sorted(self.spec["words"], key=len, reverse=True)
        self._matcher = self._match_literal
        self._first_chars = {w[0] for w in self._literals}
        self._first = lambda ch: ch in self._first_chars

    def _build_operators(self):
        self._literals = sorted(self.spec["ops"], key=len, reverse=True)
        self._matcher = self._match_literal
        self._first_chars = {o[0] for o in self._literals}
        self._first = lambda ch: ch in self._first_chars

    def _match_literal(self, text, pos):
        for lit in self._literals:
            if text.startswith(lit, pos):
                return (len(lit), None)
        return None


def load_config(path_or_dict):
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with open(path_or_dict, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Lexer:
    """Configurable lexer. Rules earlier in the config have higher priority."""

    def __init__(self, config):
        config = load_config(config)
        self.rules = [Rule(spec) for spec in config["rules"]]

    def tokenize(self, text: str):
        tokens, errors = [], []
        pos, line, col, n = 0, 1, 1, len(text)
        rules = self.rules
        candidates = {}
        while pos < n:
            ch = text[pos]
            cand = candidates.get(ch)
            if cand is None:
                cand = [r for r in rules if r.could_start(ch)]
                candidates[ch] = cand
            best_len, best_rule, best_err = 0, None, None
            for rule in cand:
                r = rule.match(text, pos)
                if r is not None and r[0] > best_len:
                    best_len, best_rule, best_err = r[0], rule, r[1]
            if best_rule is None:
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
            if not best_rule.skip:
                tokens.append(Token(best_rule.name, segment,
                                    start_line, start_col, line, col))
        return tokens, errors


def main(argv):
    import sys
    if len(argv) != 3:
        print(f"usage: {argv[0]} RULES.json SOURCE_FILE", file=sys.stderr)
        return 2
    lexer = Lexer(load_config(argv[1]))
    with open(argv[2], "r", encoding="utf-8") as fh:
        text = fh.read()
    tokens, errors = lexer.tokenize(text)
    for tok in tokens:
        print(f"{tok.start_line}:{tok.start_col}-{tok.end_line}:{tok.end_col}"
              f"\t{tok.type}\t{tok.text!r}")
    for err in errors:
        print(f"error {err.line}:{err.col}: {err.message}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
