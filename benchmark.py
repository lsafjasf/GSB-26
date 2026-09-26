"""清理耗时规模基准：python3 benchmark.py"""

import time
from cleanup_framework import CleanupStack


def bench(n, with_deps=False):
    stack = CleanupStack()
    for i in range(n):
        deps = [f"r{i-1}"] if (with_deps and i > 0) else ()
        stack.register(f"r{i}", lambda: None, depends_on=deps)
    t0 = time.perf_counter()
    report = stack.close()
    dt = time.perf_counter() - t0
    assert report.ok and len(report.executed) == n
    return dt


def main():
    print(f"{'N':>8} | {'plain (ms)':>12} | {'ns/op':>8} | "
          f"{'chained-deps (ms)':>18} | {'ns/op':>8}")
    print("-" * 68)
    for n in [0, 1, 10, 100, 1_000, 10_000, 100_000]:
        d1 = bench(n)
        d2 = bench(n, with_deps=True)
        per1 = d1 / n * 1e9 if n else 0
        per2 = d2 / n * 1e9 if n else 0
        print(f"{n:>8} | {d1*1e3:>12.3f} | {per1:>8.0f} | "
              f"{d2*1e3:>18.3f} | {per2:>8.0f}")


if __name__ == "__main__":
    main()
