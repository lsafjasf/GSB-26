#!/usr/bin/env python3
"""Differential fuzzer: main lexer (regex engine) vs reference lexer
(hand-written scanner). Random source snippets must produce identical
token streams (type, text, start/end positions) and identical error lists.
"""
import argparse
import random
import sys

from lexer import Lexer, load_config
from reference_lexer import RefLexer

KEYWORDS = ["if", "else", "while", "for", "return", "int", "float", "char",
            "void", "break", "continue"]
OPERATORS = ["<<=", ">>=", "==", "!=", "<=", ">=", "&&", "||", "++", "--",
             "+=", "-=", "*=", "/=", "<<", ">>", "+", "-", "*", "/", "%",
             "=", "<", ">", "!", "&", "|", "^", "~", "(", ")", "{", "}",
             "[", "]", ";", ",", ".", "?", ":"]
IDENT_CHARS = "abcXYZ_09" + "é中日λж文"
ILLEGAL_CHARS = ["@", "#", "$", "`", "\x07", "\\"]


def gen_identifier(rng):
    if rng.random() < 0.02:
        return "".join(rng.choice(IDENT_CHARS) for _ in range(rng.randint(50, 300)))
    return (rng.choice("abcXYZ_é中λ") +
            "".join(rng.choice(IDENT_CHARS) for _ in range(rng.randint(0, 10))))


def gen_number(rng):
    kind = rng.randrange(8)
    if kind == 0:
        return str(rng.randint(0, 10**9))
    if kind == 1:
        return f"{rng.randint(0, 999)}.{rng.randint(0, 999)}"
    if kind == 2:
        return "0x" + "".join(rng.choice("0123456789abcdefABCDEF")
                              for _ in range(rng.randint(1, 6)))
    if kind == 3:
        return f"{rng.randint(1, 99)}e{rng.choice(['', '+', '-'])}{rng.randint(0, 99)}"
    if kind == 4:
        return "_".join(str(rng.randint(0, 999)) for _ in range(rng.randint(1, 3)))
    if kind == 5:
        return rng.choice(["1e", "0x", "1.", "1__2", "1e+", "0xG", "12_"])
    if kind == 6:
        return f"{rng.randint(0, 9)}.{rng.randint(0, 9)}e{rng.randint(0, 9)}"
    return str(rng.randint(0, 9))


def gen_string(rng):
    quote = rng.choice(['"', "'"])
    body = []
    for _ in range(rng.randint(0, 12)):
        r = rng.random()
        if r < 0.15:
            body.append("\\" + rng.choice(['n', 't', '\\', '"', "'", 'x', '0']))
        elif r < 0.25:
            body.append(rng.choice("ab 中é;+*/(){}"))
        else:
            body.append(rng.choice("abc 123_;."))
    text = quote + "".join(body)
    if rng.random() < 0.12:  # unterminated variants
        tail = rng.random()
        if tail < 0.4:
            return text  # missing closing quote
        if tail < 0.7:
            return text + "\\"  # dangling escape
        return text + "\n" + quote  # newline before close (still unterminated)
    return text + quote


def gen_comment(rng):
    if rng.random() < 0.4:
        body = "".join(rng.choice("abc */{}é中\n".replace("\n", ""))
                       for _ in range(rng.randint(0, 15)))
        return "//" + body
    depth = rng.randint(0, 4)
    inner = " x ".join("/* inner */" for _ in range(depth))
    text = "/* " + inner + " tail "
    if rng.random() < 0.12:
        return text  # unterminated
    return text + "*/" * (1 if depth == 0 else 1)


def gen_whitespace(rng):
    return "".join(rng.choice(" \t\n\r  \n") for _ in range(rng.randint(1, 6)))


def gen_source(rng):
    parts = []
    for _ in range(rng.randint(1, 60)):
        r = rng.random()
        if r < 0.22:
            parts.append(gen_identifier(rng))
        elif r < 0.38:
            parts.append(gen_number(rng))
        elif r < 0.50:
            parts.append(gen_string(rng))
        elif r < 0.60:
            parts.append(gen_comment(rng))
        elif r < 0.74:
            parts.append(rng.choice(OPERATORS))
        elif r < 0.82:
            parts.append(rng.choice(KEYWORDS))
        elif r < 0.86:
            parts.append(rng.choice(ILLEGAL_CHARS))
        else:
            parts.append(gen_whitespace(rng))
        if rng.random() < 0.6:
            parts.append(gen_whitespace(rng))
    return "".join(parts)


def compare(lexer, ref, text, label):
    t1, e1 = lexer.tokenize(text)
    t2, e2 = ref.tokenize(text)
    k1 = [t.key() for t in t1]
    k2 = [t.key() for t in t2]
    if k1 != k2 or [e.key() for e in e1] != [e.key() for e in e2]:
        print(f"MISMATCH in {label}", file=sys.stderr)
        print(f"source: {text!r}", file=sys.stderr)
        for i, (a, b) in enumerate(zip(k1, k2)):
            if a != b:
                print(f"first token diff at #{i}:\n  main: {a}\n  ref:  {b}",
                      file=sys.stderr)
                break
        else:
            if len(k1) != len(k2):
                print(f"token count differs: {len(k1)} vs {len(k2)}",
                      file=sys.stderr)
        ek1 = [e.key() for e in e1]
        ek2 = [e.key() for e in e2]
        if ek1 != ek2:
            print(f"errors differ:\n  main: {ek1}\n  ref:  {ek2}",
                  file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="rules.json")
    ap.add_argument("--iterations", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20260926)
    args = ap.parse_args()

    config = load_config(args.config)
    lexer, ref = Lexer(config), RefLexer(config)
    rng = random.Random(args.seed)

    edge_cases = [
        "", " ", "\n\n\t \r\n", "é中变量 = 1;",
        "/*" * 50 + "x" + "*/" * 50,
        "a" * 10000,
        '"unterminated', "'dangling\\", "/* never closed",
        "@#$`", "1e 0x 1__2 12_", "if iffy if_",
    ]
    for i, src in enumerate(edge_cases):
        if not compare(lexer, ref, src, f"edge#{i}"):
            return 1

    for i in range(args.iterations):
        src = gen_source(rng)
        if not compare(lexer, ref, src, f"iter#{i}"):
            return 1
    print(f"OK: {len(edge_cases)} edge cases + {args.iterations} random "
          f"sources, token streams and errors identical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
