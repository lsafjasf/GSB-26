"""Independent reference lexer used for differential testing (对拍).

Deliberately written in a different style from lexer.py:
* positions are derived from a precomputed table of line-start offsets
  (bisect) instead of incrementally tracked line/col counters;
* the scan loop picks the winning candidate by sorting instead of running
  a running-best comparison;
* string/comment scanning is implemented as explicit index walks.

It must implement the SAME documented semantics (longest match, priority
tie-break, escape/unterminated handling, illegal-character recovery).
"""

from __future__ import annotations

import bisect
import re
from typing import Any


def _line_table(src: str) -> list[int]:
    starts = [0]
    for m in re.finditer("\n", src):
        starts.append(m.end())
    return starts


def _line_col(starts: list[int], offset: int) -> tuple[int, int]:
    line = bisect.bisect_right(starts, offset) - 1
    return line + 1, offset - starts[line] + 1


def tokenize(src: str, config: dict[str, Any]):
    rules = []
    for i, r in enumerate(config.get("rules", [])):
        rules.append((r["name"], re.compile(r["pattern"]),
                      bool(r.get("skip", False)), int(r.get("priority", 0)), i))
    keywords = frozenset(config.get("keywords", []))
    keyword_token = config.get("keyword_token", "KEYWORD")
    ident_type = config.get("ident_type", "IDENT")

    s = config.get("strings") or {}
    delims = sorted(s.get("delimiters", []), key=len, reverse=True)
    escape = s.get("escape", "\\")
    multiline = bool(s.get("multiline", False))
    str_token = s.get("token", "STRING")
    str_pri = int(s.get("priority", 0))

    c = config.get("comments") or {}
    line_markers = sorted(c.get("line", []), key=len, reverse=True)
    blocks = sorted(c.get("block", []), key=lambda b: len(b["start"]), reverse=True)
    comment_token = c.get("token", "COMMENT")
    comment_skip = bool(c.get("skip", False))
    comment_pri = int(c.get("priority", 0))

    starts = _line_table(src)
    n = len(src)
    pos = 0
    tokens = []   # (type, text, sline, scol, eline, ecol)
    errors = []   # (message, line, col, text)

    def scan_string(delim):
        nonlocal pos
        i = pos + len(delim)
        while i < n:
            ch = src[i]
            if escape and src.startswith(escape, i):
                i += len(escape) + (1 if i + len(escape) < n else 0)
                continue
            if src.startswith(delim, i):
                return i + len(delim), None
            if ch == "\n" and not multiline:
                return i, "unterminated string"
            i += 1
        return n, "unterminated string"

    def scan_block(start, end, nested):
        i = pos + len(start)
        depth = 1
        while i < n:
            if src.startswith(end, i):
                depth -= 1
                i += len(end)
                if depth == 0:
                    return i, None
            elif nested and src.startswith(start, i):
                depth += 1
                i += len(start)
            else:
                i += 1
        return n, "unterminated comment"

    while pos < n:
        candidates = []  # (length, -priority, -order, ttype, skip, error)
        for marker in line_markers:
            if src.startswith(marker, pos):
                nl = src.find("\n", pos)
                endpos = n if nl == -1 else nl
                candidates.append((endpos - pos, -comment_pri, 0,
                                   comment_token, comment_skip, None))
                break
        for blk in blocks:
            if src.startswith(blk["start"], pos):
                endpos, err = scan_block(blk["start"], blk["end"],
                                         bool(blk.get("nested", False)))
                candidates.append((endpos - pos, -comment_pri, -1,
                                   comment_token, comment_skip, err))
                break
        for delim in delims:
            if src.startswith(delim, pos):
                endpos, err = scan_string(delim)
                candidates.append((endpos - pos, -str_pri, -2,
                                   str_token, False, err))
                break
        for name, rx, skip, pri, order in rules:
            m = rx.match(src, pos)
            if m and m.end() > pos:
                candidates.append((m.end() - pos, -pri, -(3 + order),
                                   name, skip, None))

        if not candidates:
            line, col = _line_col(starts, pos)
            errors.append(("illegal character", line, col, src[pos]))
            pos += 1
            continue

        length, _p, _o, ttype, skip, err = max(candidates, key=lambda t: t[:3])
        text = src[pos:pos + length]
        if ttype == ident_type and text in keywords:
            ttype = keyword_token
        sline, scol = _line_col(starts, pos)
        pos += length
        eline, ecol = _line_col(starts, pos)
        if err:
            errors.append((err, sline, scol, text))
        if not skip:
            tokens.append((ttype, text, sline, scol, eline, ecol))

    return tokens, errors
