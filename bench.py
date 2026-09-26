"""百万条记录排序性能测试。运行：python3 bench.py"""

import random
import time

from multisort import KeySpec, sort_multi


def bench(name, records, specs):
    start = time.perf_counter()
    out = sort_multi(records, specs)
    elapsed = time.perf_counter() - start
    print(f"{name:<44} {elapsed:8.3f} s  ({len(out):,} 条)")
    return elapsed


def main():
    n = 1_000_000
    rng = random.Random(42)
    print(f"记录数: {n:,}  Python: {__import__('sys').version.split()[0]}")

    records = [
        (rng.randint(0, 10_000), rng.random(), rng.choice(["alpha", "beta", "gamma", "delta"]))
        for _ in range(n)
    ]
    bench("3 键 (int asc, float desc, str asc)", records,
          [KeySpec(0), KeySpec(1, reverse=True), KeySpec(2)])

    records_null = [
        (rng.choice([None] + list(range(100))), rng.random())
        for _ in range(n)
    ]
    bench("2 键含 None (int nulls=first, float desc)", records_null,
          [KeySpec(0, nulls="first"), KeySpec(1, reverse=True)])

    long_str = "s" * 500
    records_str = [(long_str + str(rng.randint(0, 999)).zfill(3), i) for i in range(n)]
    bench("1 键超长字符串 (500+ 字符)", records_str, [KeySpec(0)])

    records_dict = [
        {"dept": rng.randint(0, 50), "salary": rng.randint(3, 100) * 1000.5, "name": f"u{rng.randint(0, 9999)}"}
        for _ in range(n)
    ]
    bench("3 键 dict 记录 (dept, salary desc, name)", records_dict,
          [KeySpec("dept"), KeySpec("salary", reverse=True), KeySpec("name")])

    records_cmp = [(rng.randint(-10_000, 10_000),) for _ in range(n)]
    bench("1 键自定义 cmp (按绝对值, cmp_to_key 路径)", records_cmp,
          [KeySpec(0, cmp=lambda a, b: (abs(a) > abs(b)) - (abs(a) < abs(b)))])


if __name__ == "__main__":
    main()
