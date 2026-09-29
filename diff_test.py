"""对拍脚本：numfmt（Fraction 实现） vs 独立参照实现（Decimal 高精度）。

参照实现故意走另一条技术路线，且不导入 numfmt 的任何内部函数：
- 解析独立：str 由 decimal 模块自己的解析器处理（含指数写法与负零符号），
  float 经 Decimal 的二进制精确展开，Fraction 在 800 位精度下相除；
  numfmt 一侧的解析偏差（浮点转精确值、指数写法、负零）会被对拍发现
- 舍入用 decimal.Decimal.quantize（ROUND_HALF_UP / ROUND_HALF_EVEN）
- 每个参照结果再用 Fraction 精确校验一次（最近邻 + 半值规则），
  校验基准由参照自己的解析结果推出，不经过 numfmt 的解析代码
- 随机覆盖多语言、多精度、多分组、科学计数法阈值

运行：python3 diff_test.py [用例数，默认 20000]
"""
import random
import sys
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

from numfmt import (
    FormatSpec,
    LocaleConfig,
    PrecisionMode,
    RoundingMode,
    format_number,
    load_locales,
)

# ------------------------------------------------------------ 随机空间定义
# coverage_stats.py 从这些常量统计真实覆盖数字，改动此处即改动统计结果。

REF_PREC = 800  # 参照 Decimal 精度：远超本测试输入的有效位数与精度需求

PRECISION_DECIMAL_PLACES = range(0, 26)  # 小数位精度取值
PRECISION_SIGNIFICANT = range(1, 26)     # 有效数字精度取值
GROUP_WIDTHS = [(3,), (3, 2), (2,), (4,), (3, 2, 2)]

# ------------------------------------------------------------ 参照实现

def ref_parse(value):
    """参照实现的独立解析，走 Decimal 表示（numfmt 走 Fraction 表示）。

    返回 (是否负, |值| 的 Decimal)。str 由 decimal 模块自己的解析器处理，
    指数写法与负零符号由此独立判定；float 按其二进制精确值展开为十进制；
    Fraction 在 REF_PREC 位精度下相除（本测试输入的分母与舍入边界之间的
    最小非零间距 >= 1e-45，800 位截断可证不跨越任何舍入边界）。
    """
    if isinstance(value, bool):
        raise TypeError("不支持 bool 类型")
    if isinstance(value, Fraction):
        with localcontext() as ctx:
            ctx.prec = REF_PREC
            dec = Decimal(value.numerator) / Decimal(value.denominator)
    elif isinstance(value, str):
        dec = Decimal(value.strip())
    elif isinstance(value, (int, float, Decimal)):
        dec = Decimal(value)
    else:
        raise TypeError(f"不支持的类型: {type(value).__name__}")
    if not dec.is_finite():
        raise ValueError("不支持 NaN / Inf")
    return dec.is_signed(), dec.copy_abs()  # copy_abs 不经上下文，保持精确


def ref_exact(value, dec):
    """精确校验基准：Fraction 输入即精确值本身（无需解析）；
    其余输入由参照自己的 Decimal 解析结果精确转换，不经过 numfmt。"""
    if isinstance(value, Fraction):
        return abs(value)
    return Fraction(dec)


def ref_round(dec, exact, spec):
    """用 Decimal.quantize 高精度舍入，返回 (数字串, 指数)；并用 Fraction 精确校验。"""
    if spec.precision_mode is PrecisionMode.DECIMAL_PLACES:
        exp = -spec.precision
    elif dec == 0:
        exp = -(spec.precision - 1)
    else:
        exp = dec.adjusted() - spec.precision + 1
    if dec == 0:
        return "0", exp
    with localcontext() as ctx:
        ctx.prec = REF_PREC
        mode = ROUND_HALF_UP if spec.rounding is RoundingMode.HALF_UP else ROUND_HALF_EVEN
        q = dec.quantize(Decimal(1).scaleb(exp), rounding=mode)
    digits = "".join(str(x) for x in q.as_tuple().digits)
    if spec.precision_mode is PrecisionMode.SIGNIFICANT and len(digits) > spec.precision:
        digits = digits[:-1]  # 进位多出的末位必为 0
        exp += 1
    # ---- 精确校验：结果必须是最近邻，半值时必须满足对应规则 ----
    m = int(digits)
    unit = Fraction(10) ** exp
    value = m * unit
    diff = abs(value - exact)
    assert 2 * diff <= unit, f"参照实现舍入误差超过半值: {exact} {spec}"
    if 2 * diff == unit:
        if spec.rounding is RoundingMode.HALF_UP:
            assert value > exact, "HALF_UP 半值必须远离零进位"
        else:
            assert m % 2 == 0, "HALF_EVEN 半值必须取偶"
    return digits, exp


def ref_group(int_part, widths, sep):
    out = []
    level = 0
    while int_part:
        w = widths[min(level, len(widths) - 1)]
        out.append(int_part[-w:])
        int_part = int_part[:-w]
        level += 1
    return sep.join(reversed(out))


def ref_format(negative, dec, exact, spec, loc):
    digits, exp = ref_round(dec, exact, spec)
    if digits == "0" and not spec.keep_negative_zero:
        negative = False
    e = exp + len(digits) - 1
    use_sci = digits != "0" and (
        (spec.sci_high is not None and e >= spec.sci_high)
        or (spec.sci_low is not None and e <= spec.sci_low)
    )
    if use_sci:
        body = digits[0] + (loc.decimal_sep + digits[1:] if len(digits) > 1 else "")
        body += f"{spec.sci_marker}{'+' if e >= 0 else '-'}{abs(e)}"
    else:
        if exp < 0:
            s = digits.rjust(-exp + 1, "0")
            int_part, frac_part = s[:exp], s[exp:]
        else:
            int_part, frac_part = digits + "0" * exp, ""
        if spec.group and loc.group_sep:
            int_part = ref_group(int_part, loc.group_width, loc.group_sep)
        body = int_part + (loc.decimal_sep + frac_part if frac_part else "")
    if loc.currency_symbol:
        gap = " " if loc.currency_space else ""
        body = (
            loc.currency_symbol + gap + body
            if loc.currency_position == "prefix"
            else body + gap + loc.currency_symbol
        )
    if negative:
        return "(" + body + ")" if loc.negative_parens else loc.negative_sign + body
    return loc.positive_sign + body


# ------------------------------------------------------------ 随机用例

def random_value(rng):
    kind = rng.randrange(6)
    if kind == 0:  # 十进制字符串（含超长小数、科学记号、负零）
        ip = "".join(rng.choice("0123456789") for _ in range(rng.randrange(0, 30)))
        fp = "".join(rng.choice("0123456789") for _ in range(rng.randrange(0, 80)))
        if not ip and not fp:
            ip = "0"
        s = ip + ("." + fp if rng.random() < 0.8 or fp else "")
        if rng.random() < 0.3:
            s += "e" + str(rng.randrange(-40, 41))
        if rng.random() < 0.5:
            s = "-" + s
        return s
    if kind == 1:  # 随机 float（含次正规数、极值、负零）
        special = [0.0, -0.0, 5e-324, 1e-300, 2.675, 0.1,
                   1.7976931348623157e308, -1.5, 9999.5]
        if rng.random() < 0.3:
            return rng.choice(special)
        return rng.uniform(-1, 1) * 10.0 ** rng.randrange(-30, 31)
    if kind == 2:  # 大整数
        return rng.randrange(-10 ** 40, 10 ** 40)
    if kind == 3:  # 有理数（含非循环/循环分母）
        den = rng.choice([3, 6, 7, 9, 11, 13, 97, 10 ** rng.randrange(1, 20),
                          2 ** rng.randrange(1, 40), 7 * 10 ** 10])
        return Fraction(rng.randrange(-10 ** 25, 10 ** 25), den)
    if kind == 4:  # 恰好半值构造：整数 + 0.5 个单位
        p = rng.randrange(0, 25)
        base = rng.randrange(0, 10 ** 12)
        return Fraction(2 * base + 1, 2 * 10 ** p)
    # Decimal
    return Decimal(rng.randrange(-10 ** 20, 10 ** 20)) / Decimal(10 ** rng.randrange(0, 15))


def random_spec(rng):
    mode = rng.choice([PrecisionMode.DECIMAL_PLACES, PrecisionMode.SIGNIFICANT])
    precision = rng.choice(
        PRECISION_DECIMAL_PLACES if mode is PrecisionMode.DECIMAL_PLACES
        else PRECISION_SIGNIFICANT
    )
    sci_high = rng.choice([None, None, rng.randrange(3, 15)])
    sci_low = rng.choice([None, None, -rng.randrange(2, 9)])
    return FormatSpec(
        precision_mode=mode,
        precision=precision,
        rounding=rng.choice([RoundingMode.HALF_UP, RoundingMode.HALF_EVEN]),
        sci_high=sci_high,
        sci_low=sci_low,
        group=rng.random() < 0.8,
        keep_negative_zero=rng.random() < 0.7,
    )


def random_locale(rng, locales):
    if rng.random() < 0.5:
        return rng.choice(list(locales.values()))
    decimal_sep = rng.choice([".", ",", "·"])
    group_sep = rng.choice([s for s in [",", ".", " ", "'", "_"] if s != decimal_sep])
    return LocaleConfig(
        decimal_sep=decimal_sep,
        group_sep=group_sep,
        group_width=rng.choice(GROUP_WIDTHS),
        negative_sign=rng.choice(["-", "−"]),
        positive_sign=rng.choice(["", "+"]),
        negative_parens=rng.random() < 0.2,
        currency_symbol=rng.choice(["", "$", "€", "¥"]),
        currency_position=rng.choice(["prefix", "suffix"]),
        currency_space=rng.random() < 0.5,
    )


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    rng = random.Random(20260927)
    locales = load_locales("locales.json")
    mismatches = 0
    for i in range(n):
        value = random_value(rng)
        spec = random_spec(rng)
        loc = random_locale(rng, locales)
        got = format_number(value, spec, loc)
        negative, dec = ref_parse(value)
        want = ref_format(negative, dec, ref_exact(value, dec), spec, loc)
        if got != want:
            mismatches += 1
            print(f"[不一致 #{mismatches}] value={value!r}")
            print(f"  spec={spec}")
            print(f"  locale={loc}")
            print(f"  numfmt={got!r}  ref={want!r}")
            if mismatches >= 10:
                break
    if mismatches:
        print(f"失败：{n} 例中发现 {mismatches} 例不一致")
        sys.exit(1)
    print(f"通过：{n} 个随机用例（多语言/多精度/多舍入/科学计数法阈值）全部一致")


if __name__ == "__main__":
    main()
