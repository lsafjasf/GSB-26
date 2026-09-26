"""Configurable lexer library (Python 3, standard library only).

Lexical rules are declared by a config dict (or JSON file):

    {
      "rules": [
        {"name": "WHITESPACE", "pattern": "[ \\t\\r\\n]+", "skip": true, "priority": 0},
        {"name": "NUMBER",     "pattern": "\\d+",          "priority": 0},
        ...
      ],
      "keywords": ["if", "else"],            # retypes IDENT tokens -> KEYWORD
      "keyword_token": "KEYWORD",
      "strings":  {"delimiters": ["\"", "'"], "escape": "\\",
                   "multiline": false, "token": "STRING", "priority": 0},
      "comments": {"line": ["//"],
                   "block": [{"start": "/*", "end": "*/", "nested": true}],
                   "token": "COMMENT", "skip": false, "priority": 0}
    }

Tokenization semantics
----------------------
* At every position all candidates (line comments, block comments, strings,
  regex rules) are tried. The LONGEST match wins; ties are broken by lower
  "priority" number, then by declaration order (comments, strings, rules).
* Every token carries 1-based start/end line & column plus its raw text.
  The end position is exclusive (points just past the last character).
* Strings: the escape character consumes the next character verbatim, so
  "\\"" does not terminate a string. An unterminated string (newline when
  multiline=false, or EOF) yields a STRING token up to that point plus an
  "unterminated string" error.
* Block comments may nest when "nested": true. An unterminated block comment
  yields a COMMENT token up to EOF plus an "unterminated comment" error.
* Error recovery: a character matching nothing is reported as an
  "illegal character" error, skipped, and scanning continues. tokenize()
  always returns (tokens, errors) and never raises on bad input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Token:
    type: str
    text: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    offset: int

    def pos(self):
        return (self.type, self.text, self.start_line, self.start_col,
                self.end_line, self.end_col)


@dataclass
class LexError:
    message: str
    line: int
    col: int
    offset: int
    text: str = ""

    def pos(self):
        return (self.message, self.line, self.col, self.text)


class _Rule:
    __slots__ = ("name", "regex", "skip", "priority", "order")

    def __init__(self, name, regex, skip, priority, order):
        self.name = name
        self.regex = regex
        self.skip = skip
        self.priority = priority
        self.order = order


class Lexer:
    def __init__(self, config: dict[str, Any]):
        self._rules: list[_Rule] = []
        for i, r in enumerate(config.get("rules", [])):
            self._rules.append(_Rule(
                name=r["name"],
                regex=re.compile(r["pattern"]),
                skip=bool(r.get("skip", False)),
                priority=int(r.get("priority", 0)),
                order=i,
            ))
        kw = config.get("keywords", [])
        self._keywords = frozenset(kw)
        self._keyword_token = config.get("keyword_token", "KEYWORD")
        self._ident_type = config.get("ident_type", "IDENT")

        s = config.get("strings") or {}
        self._str_delims = sorted(s.get("delimiters", []), key=len, reverse=True)
        self._str_escape = s.get("escape", "\\")
        self._str_multiline = bool(s.get("multiline", False))
        self._str_token = s.get("token", "STRING")
        self._str_priority = int(s.get("priority", 0))

        c = config.get("comments") or {}
        self._line_comments = sorted(c.get("line", []), key=len, reverse=True)
        self._block_comments = sorted(
            c.get("block", []), key=lambda b: len(b["start"]), reverse=True)
        self._comment_token = c.get("token", "COMMENT")
        self._comment_skip = bool(c.get("skip", False))
        self._comment_priority = int(c.get("priority", 0))

    @classmethod
    def from_json(cls, path: str) -> "Lexer":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # -- internal scanners -------------------------------------------------

    def _scan_string(self, src: str, pos: int, delim: str) -> tuple[int, Optional[str]]:
        """Return (end_pos, error_message_or_None)."""
        i = pos + len(delim)
        n = len(src)
        esc = self._str_escape
        while i < n:
            if esc and src.startswith(esc, i):
                i += len(esc)
                if i < n:
                    i += 1  # escaped char consumed verbatim (even the delimiter)
                continue
            if src.startswith(delim, i):
                return i + len(delim), None
            if src[i] == "\n" and not self._str_multiline:
                return i, "unterminated string"
            i += 1
        return n, "unterminated string"

    def _scan_line_comment(self, src: str, pos: int, marker: str) -> int:
        end = src.find("\n", pos)
        return len(src) if end == -1 else end

    def _scan_block_comment(self, src: str, pos: int, start: str, end: str,
                            nested: bool) -> tuple[int, Optional[str]]:
        i = pos + len(start)
        n = len(src)
        depth = 1
        while i < n:
            if src.startswith(end, i):
                depth -= 1
                i += len(end)
                if depth == 0:
                    return i, None
                continue
            if nested and src.startswith(start, i):
                depth += 1
                i += len(start)
                continue
            i += 1
        return n, "unterminated comment"

    # -- main entry ----------------------------------------------------------

    def tokenize(self, src: str) -> tuple[list[Token], list[LexError]]:
        tokens: list[Token] = []
        errors: list[LexError] = []
        pos, line, col = 0, 1, 1
        n = len(src)

        def advance(text: str) -> None:
            nonlocal pos, line, col
            pos += len(text)
            nl = text.count("\n")
            if nl:
                line += nl
                col = len(text) - text.rfind("\n")
            else:
                col += len(text)

        while pos < n:
            start_pos, start_line, start_col = pos, line, col
            # candidate: (length, priority, order, kind, payload)
            best: Optional[tuple] = None

            def consider(length, priority, order, kind, payload, error=None):
                nonlocal best
                key = (length, -priority, -order)
                if length > 0 and (best is None or key > best[0]):
                    best = (key, length, kind, payload, error)

            for marker in self._line_comments:
                if src.startswith(marker, pos):
                    consider(self._scan_line_comment(src, pos, marker) - pos,
                             self._comment_priority, 0, "comment", None)
                    break
            for blk in self._block_comments:
                if src.startswith(blk["start"], pos):
                    endp, err = self._scan_block_comment(
                        src, pos, blk["start"], blk["end"],
                        bool(blk.get("nested", False)))
                    consider(endp - pos, self._comment_priority, 1, "comment", None, err)
                    break
            for delim in self._str_delims:
                if src.startswith(delim, pos):
                    endp, err = self._scan_string(src, pos, delim)
                    consider(endp - pos, self._str_priority, 2, "string", None, err)
                    break
            for rule in self._rules:
                m = rule.regex.match(src, pos)
                if m:
                    consider(m.end() - pos, rule.priority, 3 + rule.order,
                             "rule", rule)

            if best is None:
                ch = src[pos]
                errors.append(LexError("illegal character", line, col, pos, ch))
                advance(ch)
                continue

            _key, length, kind, payload, err = best
            text = src[pos:pos + length]
            if kind == "rule":
                rule = payload
                ttype = rule.name
                skip = rule.skip
                if ttype == self._ident_type and text in self._keywords:
                    ttype = self._keyword_token
            elif kind == "comment":
                ttype = self._comment_token
                skip = self._comment_skip
            else:
                ttype = self._str_token
                skip = False

            advance(text)
            if err:
                errors.append(LexError(err, start_line, start_col, start_pos, text))
            if not skip:
                tokens.append(Token(ttype, text, start_line, start_col,
                                    line, col, start_pos))
        return tokens, errors
