"""Independent reference lexer used for differential testing (对拍).

Deliberately written in a different style from lexer.py:
* positions are derived from a precomputed table of line-start offsets
  (bisect) instead of incrementally tracked line/col counters;
* the scan loop picks the winning candidate by sorting instead of running
  a running-best comparison;
* string/comment scanning is implemented as explicit index walks;
* escape decoding and error spans are implemented independently.

It implements the SAME documented semantics (longest match, priority
tie-break, escape decode / invalid-escape recovery, unterminated spans,
illegal-character recovery) and returns positional tuples rather than the
dataclasses used by lexer.py:

    token : (type, raw, decoded, sline, scol, eline, ecol, offset)
    error : (message, sline, scol, raw, eline, ecol, end_offset)
"""

from __future__ import annotations

import bisect
import re
from typing import Any

_DEFAULT_ESCAPES = {
    "\\": "\\", "\"": "\"", "'": "'", "n": "\n", "r": "\r", "t": "\t",
    "0": "\0", "b": "\b", "f": "\f", "v": "\v", "a": "\a", "\n": "\n",
}


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
    if "escapes" in s:
        escapes = dict(s["escapes"])
    elif escape:
        escapes = dict(_DEFAULT_ESCAPES)
    else:
        escapes = None
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
    tokens = []
    errors = []

    def scan_string(delim):
        i = pos + len(delim)
        out = []
        esc_errors = []
        fatal = None
        while i < n:
            ch = src[i]
            if escape and src.startswith(escape, i):
                j = i + len(escape)
                if j >= n:
                    esc_errors.append((i, n, "invalid escape", src[i:n]))
                    out.append(src[i:n])
                    i = n
                    fatal = "unterminated string"
                    break
                c2 = src[j]
                if escapes is not None and c2 not in escapes:
                    esc_errors.append((i, j + 1, "invalid escape", src[i:j + 1]))
                    out.append(c2)
                else:
                    out.append(escapes[c2] if escapes is not None
                               else src[i:j + 1])
                i = j + 1
                continue
            if src.startswith(delim, i):
                return i + len(delim), None, "".join(out), esc_errors
            if ch == "\n" and not multiline:
                return i, "unterminated string", "".join(out), esc_errors
            out.append(ch)
            i += 1
        return n, fatal or "unterminated string", "".join(out), esc_errors

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
        candidates = []  # (len, -pri, -order, ttype, skip, kind, data)
        for marker in line_markers:
            if src.startswith(marker, pos):
                nl = src.find("\n", pos)
                endpos = n if nl == -1 else nl
                candidates.append((endpos - pos, -comment_pri, 0,
                                   comment_token, comment_skip, "comment",
                                   endpos))
                break
        for blk in blocks:
            if src.startswith(blk["start"], pos):
                endpos, err = scan_block(blk["start"], blk["end"],
                                         bool(blk.get("nested", False)))
                candidates.append((endpos - pos, -comment_pri, -1,
                                   comment_token, comment_skip, "comment",
                                   (endpos, err)))
                break
        for delim in delims:
            if src.startswith(delim, pos):
                endpos, err, decoded, esc_errors = scan_string(delim)
                candidates.append((endpos - pos, -str_pri, -2,
                                   str_token, False, "string",
                                   (endpos, err, decoded, esc_errors)))
                break
        for name, rx, skip, pri, order in rules:
            m = rx.match(src, pos)
            if m and m.end() > pos:
                candidates.append((m.end() - pos, -pri, -(3 + order),
                                   name, skip, "rule", None))

        if not candidates:
            line, col = _line_col(starts, pos)
            eline, ecol = _line_col(starts, pos + 1)
            errors.append(("illegal character", line, col, src[pos],
                           eline, ecol, pos + 1))
            pos += 1
            continue

        length, _p, _o, ttype, skip, kind, data = max(
            candidates, key=lambda t: t[:3])
        start_off = pos
        text = src[pos:pos + length]
        sline, scol = _line_col(starts, pos)
        decoded = text
        fatal_err = None
        esc_errors = []
        if kind == "string":
            endpos, fatal_err, decoded, esc_errors = data
        elif kind == "comment":
            if isinstance(data, tuple):
                endpos, fatal_err = data
            else:
                endpos = data
        if ttype == ident_type and text in keywords:
            ttype = keyword_token
        pos += length
        eline, ecol = _line_col(starts, pos)
        for eoff, eend, emsg, eraw in esc_errors:
            l1, c1 = _line_col(starts, eoff)
            l2, c2 = _line_col(starts, eend)
            errors.append((emsg, l1, c1, eraw, l2, c2, eend))
        if fatal_err:
            l2, c2 = _line_col(starts, endpos)
            raw = src[start_off:endpos]
            errors.append((fatal_err, sline, scol, raw, l2, c2, endpos))
        if not skip:
            tokens.append((ttype, text, decoded, sline, scol,
                           eline, ecol, start_off))

    return tokens, errors
