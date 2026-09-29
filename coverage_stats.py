"""统计对拍的真实覆盖数字，供 README 引用。

数字全部从代码与配置中统计（不手写）：分组宽度 / 精度取值来自
diff_test.py 的随机空间定义，语言配置来自 locales.json，
精度模式与舍入规则来自 numfmt 的枚举。改动实现即改动统计结果。

运行：python3 coverage_stats.py
"""
from numfmt import PrecisionMode, RoundingMode, load_locales

from diff_test import GROUP_WIDTHS, PRECISION_DECIMAL_PLACES, PRECISION_SIGNIFICANT


def _span(r):
    return f"{r.start}–{r.stop - 1}"


def main():
    locales = load_locales("locales.json")
    print(f"精度模式: {len(PrecisionMode)} 种 "
          f"({', '.join(m.value for m in PrecisionMode)})")
    print(f"精度取值: decimal_places {_span(PRECISION_DECIMAL_PLACES)} "
          f"({len(PRECISION_DECIMAL_PLACES)} 个), "
          f"significant {_span(PRECISION_SIGNIFICANT)} "
          f"({len(PRECISION_SIGNIFICANT)} 个)")
    print(f"舍入规则: {len(RoundingMode)} 种 "
          f"({', '.join(m.value for m in RoundingMode)})")
    print(f"分组宽度: {len(GROUP_WIDTHS)} 种 "
          f"({', '.join(str(tuple(w)) for w in GROUP_WIDTHS)})")
    print(f"语言配置: {len(locales)} 套 ({', '.join(locales)})")


if __name__ == "__main__":
    main()
