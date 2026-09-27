"""性能测试：格式化 100,000 次的耗时。

运行：python3 bench.py [次数，默认 100000]
"""
import platform
import random
import sys
import time

from numfmt import FormatSpec, format_number, load_locales


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    rng = random.Random(42)
    values = []
    for _ in range(n):
        kind = rng.randrange(4)
        if kind == 0:
            values.append(rng.uniform(-1e6, 1e6))
        elif kind == 1:
            values.append(str(rng.randrange(-10 ** 12, 10 ** 12)) + "." + str(rng.randrange(10 ** 6)))
        elif kind == 2:
            values.append(rng.randrange(-10 ** 9, 10 ** 9))
        else:
            values.append(rng.uniform(-1, 1) * 10.0 ** rng.randrange(-20, 21))

    locales = load_locales("locales.json")
    cases = [
        ("默认(2位小数, en_US分组)", FormatSpec(), locales["en_US"]),
        ("有效数字6位+科学计数法阈值", FormatSpec(
            precision_mode="significant", precision=6, sci_high=6, sci_low=-4),
         locales["de_DE"]),
        ("印度式分组+货币", FormatSpec(precision=2), locales["hi_IN"]),
    ]

    print(f"环境: Python {platform.python_version()} / {platform.platform()}")
    print(f"用例: {n:,} 个混合类型数值（float / 字符串 / int）")
    for name, spec, loc in cases:
        # 预热
        for v in values[:1000]:
            format_number(v, spec, loc)
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            for v in values:
                format_number(v, spec, loc)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        print(f"{name:<28} 总计 {best*1000:8.1f} ms  单次 {best/n*1e6:6.2f} µs  "
              f"吞吐 {n/best/1e3:8.0f} K次/s")


if __name__ == "__main__":
    main()
