#!/usr/bin/env python3
"""Benchmark: time to draw 1,000,000 samples per distribution.

Run: python3 benchmark.py
"""

import platform
import random
import time

from probdist import distributions as dist

N = 1_000_000
REPEATS = 3


def bench(name, fn):
    best = float("inf")
    for _ in range(REPEATS):
        rng = random.Random(12345)
        t0 = time.perf_counter()
        fn(rng, N)
        dt = time.perf_counter() - t0
        best = min(best, dt)
    print(f"{name:<28} {best:>8.3f} s   {N / best / 1e6:>6.2f} M samples/s")
    return name, best


if __name__ == "__main__":
    print(f"Python {platform.python_version()} on {platform.machine()}, "
          f"n={N:,} per distribution, best of {REPEATS} runs\n")
    bench("uniform(0, 1)", lambda r, n: dist.sample_uniform(r, 0.0, 1.0, n))
    bench("exponential(1.5)", lambda r, n: dist.sample_exponential(r, 1.5, n))
    bench("normal(0, 1)", lambda r, n: dist.sample_normal(r, 0.0, 1.0, n))
    bench("binomial(20, 0.3)", lambda r, n: dist.sample_binomial(r, 20, 0.3, n))
