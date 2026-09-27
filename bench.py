"""性能基准：上千步骤配置的解析 + 静态校验 + 编译耗时。

运行: python3 bench.py
"""

import random
import time

from dslc import compile, execute
from dslc.testing import chain_config, random_config, wide_config


def measure(name, text, runs=3):
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        plan = compile(text)
        elapsed = time.perf_counter() - start
        timings.append(elapsed)
    best = min(timings)

    start = time.perf_counter()
    optimized = compile(text, optimize=True)
    optimized_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    trace = execute(optimized)
    exec_ms = (time.perf_counter() - start) * 1000

    print(
        f"{name:<24} steps={len(plan['steps']):>5}  "
        f"compile={best * 1000:8.2f} ms  "
        f"compile+opt={optimized_ms:8.2f} ms  "
        f"stages={len(optimized['stages']):>5}  "
        f"execute={exec_ms:7.2f} ms  executed={len(trace)}"
    )


def main():
    sizes = (1000, 2000, 4000)
    print("== deep chain (每步依赖上一步) ==")
    for n in sizes:
        measure(f"chain/{n}", chain_config(n))
    print("\n== wide fan-out (步骤互相独立) ==")
    for n in sizes:
        measure(f"wide/{n}", wide_config(n))
    print("\n== random DAG + 参数 + 条件分支 (seed=7) ==")
    for n in sizes:
        rng = random.Random(7)
        measure(f"random/{n}", random_config(rng, n))


if __name__ == "__main__":
    main()
