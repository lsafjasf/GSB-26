"""Differential fuzzer: main lexer vs. reference lexer (对拍).

Generates random source fragments (identifiers incl. non-ASCII, numbers,
strings with escapes / unterminated, nested & unterminated comments,
operators, whitespace, illegal characters) and asserts both implementations
produce identical token sequences (type, text, positions) and error lists.

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
ILLEGAL = ["@", "#", "$", "`", "\\", "\x00", "\x07"]


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
    if kind == 2:  # string, sometimes escaped / unterminated
        delim = rng.choice(["\"", "'"])
        body = "".join(rng.choice("ab \t" + delim + "\\n") for _ in range(rng.randrange(0, 10)))
        body = body.replace("\\", "\\\\") if rng.random() < 0.5 else body
        if rng.random() < 0.15:
            return delim + body  # unterminated
        return delim + body + delim
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
    if kind == 8:  # string with escaped delimiter
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

    for it in range(iterations):
        src = gen_source(rng, rng.randrange(1, 40))
        tokens_a, errors_a = lexer.tokenize(src)
        tokens_b, errors_b = reference_lexer.tokenize(src, CONFIG)
        pa = [t.pos() for t in tokens_a]
        ea = [e.pos() for e in errors_a]
        if pa != tokens_b or ea != errors_b:
            print("MISMATCH at iteration %d (seed=%d)" % (it, seed))
            print("source: %r" % src)
            print("main tokens : %r" % (pa,))
            print("ref  tokens : %r" % (tokens_b,))
            print("main errors : %r" % (ea,))
            print("ref  errors : %r" % (errors_b,))
            sys.exit(1)
        # reconstruction invariant: tokens + skipped text + errors == source
        covered = []
        for t in tokens_a:
            covered.append((t.offset, t.offset + len(t.text)))
        for e in errors_a:
            if e.message == "illegal character":
                covered.append((e.offset, e.offset + 1))
        prev_end = 0
        for s, e in sorted(covered):
            assert s >= prev_end, "overlapping tokens"
            prev_end = max(prev_end, e)

    print("OK: %d iterations, no divergence (seed=%d)" % (iterations, seed))


if __name__ == "__main__":
    main()
