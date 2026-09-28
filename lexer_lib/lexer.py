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
                   "escapes": {"n": "\\n", "t": "\\t", "\"": "\"", "'": "'",
                               "\\": "\\", "0": "\\0"},
                   "unknown_escape": "error", "multiline": false,
                   "token": "STRING", "priority": 0},
      "comments": {"line": ["//"],
                   "block": [{"start": "/*", "end": "*/", "nested": true}],
                   "token": "COMMENT", "skip": false, "priority": 0},
      "mode": "recover"        # "recover" (default) or "strict"
    }

Tokenization semantics
----------------------
* At every position all candidates (line comments, block comments, strings,
  regex rules) are tried. The LONGEST match wins; ties are broken by lower
  "priority" number, then by declaration order (comments, strings, rules).
* Every token carries 1-based start/end line & column, its byte/char offset,
  and TWO text fields: ``text`` (the raw source slice) and ``decoded``
  (for STRING tokens: delimiters stripped and escape sequences decoded;
  identical to ``text`` for every other token kind). The end position is
  exclusive (points just past the last character).
* Strings: the escape character introduces an escape sequence. Known
  sequences (configurable via "escapes"; defaults cover quote, backslash
  and newline escapes) are decoded. An invalid/unknown escape raises an
  "invalid escape" error at the backslash position; in recovery mode the
  offending character is preserved verbatim and scanning continues. A
  trailing backslash at EOF raises "invalid escape" as well. An escape
  before a newline is consumed (line continuation), so a backslashed
  newline never terminates a non-multiline string.
* Block comments may nest when "nested": true.
* Unterminated strings / block comments still yield a token up to the
  stopping point plus an error carrying BOTH the start position and the
  end position (half-open: [start, end)). String start = opening quote;
  end = the newline (excluded from the token) or EOF. Comment start = the
  opening marker; end = EOF.
* Recovery mode ("recover", default): every error is collected with its
  span and scanning resumes, so tokenize() always returns (tokens, errors)
  and never raises on bad input. Strict mode ("strict"): the first lexical
  error is raised as LexicalError (carrying the same message and span).
"""

from __future__ import annotations

import bisect
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
    decoded: str = ""

    def pos(self):
        return (self.type, self.text, self.start_line, self.start_col,
                self.end_line, self.end_col)

    def full(self):
        return (self.type, self.text, self.decoded, self.start_line,
                self.start_col, self.end_line, self.end_col, self.offset)


@dataclass
class LexError:
    message: str
    line: int
    col: int
    offset: int
    text: str = ""
    end_line: int = 0
    end_col: int = 0
    end_offset: int = -1

    def __post_init__(self):
        if self.end_line == 0:
            self.end_line, self.end_col = self.line, self.col
        if self.end_offset < 0:
            self.end_offset = self.offset

    @property
    def span(self):
        """Half-open span: ((start_line, start_col), (end_line, end_col))."""
        return (self.line, self.col), (self.end_line, self.end_col)

    def pos(self):
        return (self.message, self.line, self.col, self.text,
                self.end_line, self.end_col, self.end_offset)


class LexicalError(Exception):
    """Raised in strict mode. Carries the same message/span as LexError."""

    def __init__(self, err: LexError):
        super().__init__(err.message)
        self.err = err


def build_line_table(src: str) -> list[int]:
    starts = [0]
    for m in re.finditer("\n", src):
        starts.append(m.end())
    return starts


def loc_at(starts: list[int], offset: int) -> tuple[int, int]:
    """1-based (line, col) for the character at ``offset`` (or past-EOF)."""
    line = bisect.bisect_right(starts, offset) - 1
    return line + 1, offset - starts[line] + 1


class _Rule:
    __slots__ = ("name", "regex", "skip", "priority", "order")

    def __init__(self, name, regex, skip, priority, order):
        self.name = name
        self.regex = regex
        self.skip = skip
        self.priority = priority
        self.order = order


_DEFAULT_ESCAPES = {
    "\\": "\\",
    "\"": "\"",
    "'": "'",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "0": "\0",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "a": "\a",
    "\n": "\n",
}


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
        if "escapes" in s:
            self._str_escapes: Optional[dict[str, str]] = dict(s["escapes"])
        elif self._str_escape:
            self._str_escapes = dict(_DEFAULT_ESCAPES)
        else:
            self._str_escapes = None
        self._str_unknown = s.get("unknown_escape", "error")
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

        self._strict = config.get("mode", "recover") == "strict"

    @classmethod
    def from_json(cls, path: str) -> "Lexer":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # -- internal scanners -------------------------------------------------

    def _scan_string(self, src: str, pos: int, delim: str):
        """Return (end_pos, error_or_None, decoded, escape_errors).

        ``escape_errors`` is a list of (offset, end_offset, message, raw).
        """
        i = pos + len(delim)
        n = len(src)
        esc = self._str_escape
        esc_len = len(esc)
        decoded_parts: list[str] = []
        esc_errors: list[tuple[int, int, str, str]] = []
        fatal: Optional[str] = None

        while i < n:
            if esc and src.startswith(esc, i):
                j = i + esc_len
                if j >= n:
                    # trailing escape character at EOF
                    esc_errors.append((i, n, "invalid escape", src[i:n]))
                    decoded_parts.append(src[i:n])
                    i = n
                    fatal = "unterminated string"
                    break
                ch = src[j]
                # backslash + real newline is a line continuation: both
                # characters are consumed (mapped via the escape table when
                # declared), so the string never ends on that newline.
                if self._str_escapes is not None and ch not in self._str_escapes:
                    esc_errors.append((i, j + 1, "invalid escape", src[i:j + 1]))
                    decoded_parts.append(ch)  # recovery: keep char verbatim
                else:
                    mapping = self._str_escapes
                    decoded_parts.append(mapping[ch] if mapping is not None
                                         else src[i:j + 1])
                i = j + 1
                continue
            if src.startswith(delim, i):
                return i + len(delim), None, "".join(decoded_parts), esc_errors
            if src[i] == "\n" and not self._str_multiline:
                return i, "unterminated string", "".join(decoded_parts), esc_errors
            decoded_parts.append(src[i])
            i += 1
        return n, fatal or "unterminated string", "".join(decoded_parts), esc_errors

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
        line_table: Optional[list[int]] = None
        pos, line, col = 0, 1, 1
        n = len(src)

        def loc(offset):
            nonlocal line_table
            if line_table is None:
                line_table = build_line_table(src)
            return loc_at(line_table, offset)

        def raise_or_append(err: LexError) -> None:
            if self._strict:
                raise LexicalError(err)
            errors.append(err)

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
            # candidate: (sort_key, length, kind, payload, error, extra)
            best: Optional[tuple] = None

            def consider(length, priority, order, kind, payload,
                         error=None, extra=None):
                nonlocal best
                key = (length, -priority, -order)
                if length > 0 and (best is None or key > best[0]):
                    best = (key, length, kind, payload, error, extra)

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
                    consider(endp - pos, self._comment_priority, 1,
                             "comment", None, err, endp)
                    break
            for delim in self._str_delims:
                if src.startswith(delim, pos):
                    endp, err, decoded, esc_errors = self._scan_string(
                        src, pos, delim)
                    consider(endp - pos, self._str_priority, 2, "string",
                            delim, err, (endp, decoded, esc_errors))
                    break
            for rule in self._rules:
                m = rule.regex.match(src, pos)
                if m:
                    consider(m.end() - pos, rule.priority, 3 + rule.order,
                             "rule", rule)

            if best is None:
                ch = src[pos]
                end_off = pos + len(ch)
                el, ec = loc(end_off)
                raise_or_append(LexError("illegal character", line, col, pos,
                                         ch, el, ec, end_off))
                advance(ch)
                continue

            _key, length, kind, payload, err, extra = best
            text = src[pos:pos + length]
            esc_errors: list = []
            decoded = text
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
                _endp, decoded, esc_errors = extra

            advance(text)
            for eoff, eend, emsg, eraw in esc_errors:
                sl, sc = loc(eoff)
                el, ec = loc(eend)
                raise_or_append(LexError(emsg, sl, sc, eoff, eraw,
                                         el, ec, eend))
            if err:
                if kind == "string":
                    end_off = extra[0]
                else:
                    end_off = extra if kind == "comment" else start_pos + length
                el, ec = loc(end_off)
                raise_or_append(LexError(err, start_line, start_col,
                                         start_pos, src[start_pos:end_off],
                                         el, ec, end_off))
            if not skip:
                tokens.append(Token(ttype, text, start_line, start_col,
                                    line, col, start_pos, decoded))
        return tokens, errors
