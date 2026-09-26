"""性能脚本：格式化 100,000 次的耗时。用法：python3 bench.py [次数]"""
import json
import random
import sys
import time

import numfmt

CFG = json.load(open("locales.json", encoding="utf-8"))
LOCALES = list(CFG["locales"].values())
SPECS = list(CFG["specs"].values())


def main(n=100_000):
    rng = random.Random(42)
    cases = []
    for _ in range(2000):  # 预生成 2000 组，循环复用以摊薄生成成本
        v = f"{rng.randrange(-10**9, 10**9)}.{rng.randrange(10**6):06d}"
        cases.append((v, rng.choice(SPECS), rng.choice(LOCALES)))
    # 预热
    for i in range(2000):
        numfmt.format_number(*cases[i % len(cases)])
    t0 = time.perf_counter()
    for i in range(n):
        numfmt.format_number(*cases[i % len(cases)])
    dt = time.perf_counter() - t0
    print(f"格式化 {n:,} 次：总耗时 {dt:.3f} s，"
          f"平均 {dt / n * 1e6:.2f} µs/次，{n / dt:,.0f} 次/s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
