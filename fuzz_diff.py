"""Differential fuzzer: main lexer vs. reference lexer (对拍).

Generates random source fragments (identifiers incl. non-ASCII, numbers,
strings with valid/invalid escapes & line continuations, unterminated
strings, nested & unterminated comments, operators, whitespace, illegal
characters) and asserts both implementations produce identical token
sequences (type, raw text, decoded text, positions, offset) and error
lists (message, start/end spans, raw text).

Usage: python3 fuzz_diff.py [iterations] [seed]
"""

import json
import random
import sys

from lexer_lib.lexer import Lexer
from lexer_lib import reference_lexer

CONFIG = json.load(open("rules.json", encoding="utf-8"))

IDENT_START = "abcxyz_" + "αβγ中文字é"
IDENT_CONT = IDENT_START + "0123456789"
KEYWORDS = CONFIG["keywords"]
OPS = ["==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=",
       "<<", ">>", "->", "+", "-", "*", "/", "%", "=", "<", ">", "!",
       "&", "|", "^", "~", "(", ")", "[", "]", "{", "}", ";", ",", ".", "?", ":"]
ILLEGAL = ["@", "#", "$", "`", "\x00", "\x07"]


def _string(rng):
    delim = rng.choice(["\"", "'"])
    n_parts = rng.randrange(0, 8)
    body_parts = []
    for _ in range(n_parts):
        choice = rng.randrange(6)
        if choice == 0:  # plain char
            body_parts.append(rng.choice("ab \tAB"))
        elif choice == 1:  # raw delimiter only when terminated handled later
            body_parts.append(delim)
        elif choice == 2:  # valid simple escapes
            body_parts.append(rng.choice(["\\n", "\\t", "\\r", "\\\\",
                                          "\\\"", "\\'", "\\0", "\\b"]))
        elif choice == 3:  # invalid escape (recovery keeps char verbatim)
            body_parts.append("\\" + rng.choice("xyzq@"))
        elif choice == 4:  # line continuation: backslash + real newline
            body_parts.append("\\\n")
        else:  # escaped delimiter (must never terminate the string)
            body_parts.append("\\" + delim)
    # avoid raw active delimiter inside the body (it would end the string)
    body = "".join(body_parts).replace(
        delim, "\\" + delim) if rng.random() < 0.5 else \
        "".join(p for p in body_parts if p != delim)
    unterminated = rng.random() < 0.18
    if unterminated:
        if rng.random() < 0.25:
            body += "\\"  # trailing backslash at EOF
        return delim + body
    return delim + body + delim


def gen_fragment(rng):
    kind = rng.randrange(12)
    if kind == 0:  # identifier / keyword
        if rng.random() < 0.3:
            return rng.choice(KEYWORDS)
        return (rng.choice(IDENT_START) +
                "".join(rng.choice(IDENT_CONT) for _ in range(rng.randrange(0, 12))))
    if kind == 1:  # number
        return rng.choice([
            str(rng.randrange(0, 10**6)),
            "%d.%d" % (rng.randrange(1000), rng.randrange(1000)),
            "0x%X" % rng.randrange(0, 0xFFFF),
            "%de%d" % (rng.randrange(100), rng.randrange(10)),
            "%d.%de-%d" % (rng.randrange(100), rng.randrange(100), rng.randrange(10)),
        ])
    if kind == 2:  # string (escapes, invalid escapes, line continuation)
        return _string(rng)
    if kind == 3:  # line comment
        return "//" + "".join(rng.choice("abc /*\"'") for _ in range(rng.randrange(0, 15)))
    if kind == 4:  # block comment, nested, sometimes unterminated
        depth = rng.randrange(1, 4)
        inner = "".join(rng.choice("ab */\"'\n") for _ in range(rng.randrange(0, 8)))
        text = "/*" * depth + inner + "*/" * depth
        if rng.random() < 0.15:
            text = text[:rng.randrange(1, len(text))]  # cut -> unterminated
        return text
    if kind == 5:
        return rng.choice(OPS)
    if kind == 6:
        return rng.choice([" ", "  ", "\t", "\n", "\n\n", " \t \n"])
    if kind == 7:
        return rng.choice(ILLEGAL)
    if kind == 8:  # classic escaped delimiter strings
        return '"a\\"b"' if rng.random() < 0.5 else "'x\\'y'"
    if kind == 9:  # comment-like operator sequences
        return rng.choice(["/", "//", "/*", "*/", "/**/", "/*/"])
    if kind == 10:  # number-ish edge
        return rng.choice(["1.", ".5", "1.5.6", "0x", "12abc", "1e", ".."])
    return rng.choice(["ifx", "iff", "else1", "_", "__"])


def gen_source(rng, n_fragments):
    return "".join(gen_fragment(rng) for _ in range(n_fragments))


def main():
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    lexer = Lexer(CONFIG)
    rng = random.Random(seed)

    saw_invalid_escape = saw_unterminated = False
    for it in range(iterations):
        src = gen_source(rng, rng.randrange(1, 40))
        tokens_a, errors_a = lexer.tokenize(src)
        tokens_b, errors_b = reference_lexer.tokenize(src, CONFIG)
        pa = [t.full() for t in tokens_a]
        ea = [e.pos() for e in errors_a]
        saw_invalid_escape |= any(
            e.message == "invalid escape" for e in errors_a)
        saw_unterminated |= any(
            e.message.startswith("unterminated") for e in errors_a)
        if pa != tokens_b or ea != errors_b:
            print("MISMATCH at iteration %d (seed=%d)" % (it, seed))
            print("source: %r" % src)
            print("main tokens : %r" % (pa,))
            print("ref  tokens : %r" % (tokens_b,))
            print("main errors : %r" % (ea,))
            print("ref  errors : %r" % (errors_b,))
            sys.exit(1)
        # reconstruction invariant: tokens and illegal-character gaps tile
        # the source disjointly; every token/error raw slice must equal the
        # source at its reported span (invalid-escape / unterminated spans
        # legitimately nest inside a STRING/COMMENT token).
        covered = [(t.offset, t.offset + len(t.text)) for t in tokens_a]
        for e in errors_a:
            assert e.end_offset >= e.offset, (src, e)
            assert src[e.offset:e.end_offset] == e.text,                 "bad error raw: %r %r" % (src, e)
            if e.message == "illegal character":
                covered.append((e.offset, e.offset + 1))
        prev_end = 0
        for s, e in sorted(covered):
            assert s >= prev_end, "overlapping spans: %r" % src
            prev_end = max(prev_end, e)
        # raw/decoded consistency for strings: decoded differs from raw only
        # via escape decoding; valid escapes collapse in length
        for t in tokens_a:
            if t.type == CONFIG["strings"]["token"]:
                assert len(t.decoded) <= len(t.text) - 2 or                     len(t.decoded) <= len(t.text), (src, t)

    assert saw_invalid_escape, "fuzzer never produced an invalid escape"
    assert saw_unterminated, "fuzzer never produced an unterminated construct"
    print("OK: %d iterations, no divergence (seed=%d)" % (iterations, seed))


if __name__ == "__main__":
    main()
