#!/usr/bin/env python3
"""Throughput benchmark + edge-case coverage for the configurable lexer."""
import time

from lexer import Lexer, load_config
from reference_lexer import RefLexer
from fuzz_diff import gen_source
import random


def bench(name, text, lexer, repeat=3):
    best = None
    tokens = errors = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        tokens, errors = lexer.tokenize(text)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    size_mb = len(text.encode("utf-8")) / 1e6
    mbps = size_mb / best if best > 0 else float("inf")
    tps = len(tokens) / best if best > 0 else float("inf")
    print(f"{name:<28} {size_mb:>8.3f} MB  {len(tokens):>9} tokens  "
          f"{len(errors):>4} errors  {best*1000:>9.1f} ms  "
          f"{mbps:>8.1f} MB/s  {tps/1e6:>6.2f} Mtok/s")
    return tokens, errors


def main():
    config = load_config("rules.json")
    lexer = Lexer(config)
    ref = RefLexer(config)

    rng = random.Random(7)
    chunk = gen_source(random.Random(42))
    adversarial = (chunk + "\n") * max(1, (2_000_000 // (len(chunk) + 1)))

    def typical_line(r):
        names = ["alpha", "beta", "counter", "value", "result", "temp"]
        kws = ["int", "if", "while", "return", "float"]
        return (f"    {r.choice(kws)} {r.choice(names)} = "
                f"{r.randint(0, 9999)} + {r.randint(0, 99)}.{r.randint(0, 9)}; "
                f"// note\n")

    r = random.Random(11)
    typical = "".join(typical_line(r) for _ in range(28_000))

    print("== throughput (main lexer) ==")
    bench("typical code (~1MB)", typical, lexer)
    bench("adversarial mix", adversarial, lexer)
    bench("empty input", "", lexer)
    bench("whitespace only (2MB)", " \t\n" * 700_000, lexer)
    bench("long identifier (2MB)", "a" * 2_000_000, lexer)
    bench("deep nested comment (10k)", "/*" * 10_000 + "x" + "*/" * 10_000,
          lexer)
    nonascii = ("变量名 = 数值 + 1;\n" * 60_000)
    bench("non-ASCII identifiers", nonascii, lexer)

    print("\n== reference lexer (cross-check throughput) ==")
    bench("typical code (~1MB)", typical, ref)

    print("\n== edge-case correctness spot checks ==")
    cases = {
        "empty": "",
        "whitespace only": " \t\n\r\n  ",
        "long identifier": "x" * 100_000,
        "deep nested comment": "/*" * 1000 + "!" + "*/" * 1000,
        "non-ASCII ident": "变量_α = 1;",
    }
    for name, src in cases.items():
        t, e = lexer.tokenize(src)
        print(f"  {name:<22} tokens={len(t)} errors={len(e)}")


if __name__ == "__main__":
    main()
