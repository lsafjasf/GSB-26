"""Throughput benchmark. Run: python3 bench.py [target_mb]"""

import json
import random
import sys
import time

from lexer_lib.lexer import Lexer
from fuzz_diff import gen_fragment

CONFIG = json.load(open("rules.json", encoding="utf-8"))


def build_source(target_bytes):
    rng = random.Random(1234)
    checker = Lexer(CONFIG)
    parts, size = [], 0
    while size < target_bytes:
        frag = gen_fragment(rng)
        # keep the benchmark well-formed: unterminated strings/comments
        # would swallow the rest of the file
        _, errs = checker.tokenize(frag + "\nx")
        if errs:
            continue
        parts.append(frag)
        size += len(frag) + 1
    return "\n".join(parts)


def main():
    target_mb = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    src = build_source(int(target_mb * 1024 * 1024))
    lexer = Lexer(CONFIG)

    # warmup + correctness count
    tokens, errors = lexer.tokenize(src)
    n_tokens = len(tokens)

    best = None
    for _ in range(3):
        t0 = time.perf_counter()
        tokens, errors = lexer.tokenize(src)
        dt = time.perf_counter() - t0
        best = dt if best is None or dt < best else best

    mb = len(src.encode("utf-8")) / (1024 * 1024)
    print("source size   : %.2f MiB (%d chars)" % (mb, len(src)))
    print("tokens        : %d (errors: %d)" % (n_tokens, len(errors)))
    print("best of 3     : %.3f s" % best)
    print("throughput    : %.2f MiB/s, %.1f M tokens/s"
          % (mb / best, n_tokens / best / 1e6))


if __name__ == "__main__":
    main()
