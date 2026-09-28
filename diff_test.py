"""对拍脚本：numfmt（被测库） vs 独立参照实现（Decimal 高精度）。

参照实现与被测库完全独立，尤其不导入 numfmt 的任何私有解析函数：
- 输入解析由本文件自行完成，内部统一使用 decimal.Decimal 表示（另一套数值表示）：
    * str     —— 手写十进制/指数词法（不用 numfmt 的正则与分数构造）
    * float   —— 解析 float.hex() 的二进制尾数与 2 的幂精确还原，
                 再与 Decimal(float) 这条独立路线互校（覆盖 -0.0、次正规数）
    * Decimal / int —— 按十进制语义直接构造
    * Fraction —— 为支持该输入类型，按分子/分母高精度换算并以 Fraction 精确校验
- 舍入用 decimal.Decimal.quantize（ROUND_HALF_UP / ROUND_HALF_EVEN）
- 每个参照结果再用精确有理数校验一次（最近邻 + 半值规则）
- 随机覆盖多语言、多精度、多分组、科学计数法阈值
- 结束时打印实际覆盖统计（分组宽度种类、语言配置套数等均由运行时统计产出）

运行：python3 diff_test.py [用例数，默认 20000]
"""
import math
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

# ------------------------------------------------------------ 参照解析
# 以下解析代码完全独立于 numfmt._to_signed_fraction，数值表示走 Decimal
# 路线；被测库解析层（字符串 / 浮点 / 十进制 / 分数）的偏差不会被带入
# 参照结果，对拍因此具备鉴别力。


def ref_parse_str(text):
    """独立解析十进制字符串（含指数写法与负零），返回 (negative, Decimal)。"""
    s = text.strip()
    if not s:
        raise ValueError(f"无法解析的数值字符串: {text!r}")
    negative = False
    if s[0] in "+-":
        negative = s[0] == "-"
        s = s[1:]
    coeff = ""
    frac_len = 0
    seen_dot = False
    i = 0
    while i < len(s) and (s[i].isdigit() or s[i] == "."):
        if s[i] == ".":
            if seen_dot:
                raise ValueError(f"无法解析的数值字符串: {text!r}")
            seen_dot = True
        else:
            coeff += s[i]
            if seen_dot:
                frac_len += 1
        i += 1
    exponent = 0
    if i < len(s):
        if s[i] not in "eE" or i + 1 >= len(s):
            raise ValueError(f"无法解析的数值字符串: {text!r}")
        j = i + 1
        exp_negative = False
        if s[j] in "+-":
            exp_negative = s[j] == "-"
            j += 1
        if j >= len(s) or not s[j:].isdigit():
            raise ValueError(f"无法解析的数值字符串: {text!r}")
        exponent = int(s[j:])
        if exp_negative:
            exponent = -exponent
    if not coeff:
        raise ValueError(f"无法解析的数值字符串: {text!r}")
    decimal_exp = exponent - frac_len
    if int(coeff) == 0:
        magnitude = Decimal(0)
    else:
        magnitude = Decimal((0, tuple(int(c) for c in coeff), decimal_exp))
    return negative, magnitude


def ref_parse_float(value):
    """精确还原 float 的二进制值，返回 (negative, Decimal)。"""
    if math.isnan(value) or math.isinf(value):
        raise ValueError("不支持 NaN / Inf")
    negative = math.copysign(1.0, value) < 0
    hexa = float.hex(abs(value))  # 形如 0x1.8p+3 / 0x0.000...1p-1022
    mant, pexp = hexa[2:].split("p")
    head, _, tail = mant.partition(".")
    significand = int((head + tail) or "0", 16)
    shift = int(pexp) - 4 * len(tail)
    with localcontext() as ctx:
        ctx.prec = 800
        if shift >= 0:
            magnitude = Decimal(significand) * Decimal(2) ** shift
        else:
            magnitude = Decimal(significand) / Decimal(2) ** (-shift)
    # 另一条彼此独立的转换路线（C 库 float -> Decimal）交叉验证
    assert magnitude == Decimal(abs(value)), "参照实现 float 解析两条路线不一致"
    return negative, magnitude


def ref_parse_decimal(value):
    if not value.is_finite():
        raise ValueError("不支持 NaN / Inf")
    return value.is_signed(), Decimal(abs(value))


def ref_parse_fraction(value):
    a = abs(value)
    with localcontext() as ctx:
        ctx.prec = max(80, len(str(a.numerator)) + len(str(a.denominator)) + 80)
        magnitude = Decimal(a.numerator) / Decimal(a.denominator)
    return value < 0, magnitude


def ref_parse_int(value):
    return value < 0, Decimal(abs(value))


def ref_exact(value):
    """独立得到输入绝对值的精确有理数（用于校验参照舍入），同样不调用 numfmt。"""
    if isinstance(value, Fraction):
        return abs(value)
    if isinstance(value, int):
        return Fraction(abs(value))
    if isinstance(value, Decimal):
        return Fraction(abs(value))
    if isinstance(value, str):
        return Fraction(ref_parse_str(value)[1])
    # float：由二进制展开式精确得到（与 ref_parse_float 相同的构造，避开浮点误差）
    _, magnitude = ref_parse_float(value)
    return Fraction(magnitude)


def ref_parse(value):
    """独立解析输入：返回 (negative, magnitude: Decimal, exact: Fraction)。

    解析完全由参照侧自行完成，numfmt 解析层的偏差不会传入参照结果。
    """
    if isinstance(value, bool):
        raise TypeError("不支持 bool 类型")
    if isinstance(value, int):
        negative, magnitude = ref_parse_int(value)
    elif isinstance(value, Fraction):
        negative, magnitude = ref_parse_fraction(value)
    elif isinstance(value, float):
        negative, magnitude = ref_parse_float(value)
    elif isinstance(value, Decimal):
        negative, magnitude = ref_parse_decimal(value)
    elif isinstance(value, str):
        negative, magnitude = ref_parse_str(value)
    else:
        raise TypeError(f"不支持的类型: {type(value).__name__}")
    return negative, magnitude, ref_exact(value)


# ------------------------------------------------------------ 参照舍入 / 排版

def ref_decimal_exponent(d):
    """十进制指数：10**e <= d < 10**(e+1)（d > 0），Decimal 估算 + 精确校正。"""
    with localcontext() as ctx:
        ctx.prec = max(
            100,
            len(d.as_tuple().digits) + abs(d.as_tuple().exponent) + 80,
        )
        e = d.adjusted()
        one = Decimal(1)
        while d >= one.scaleb(e + 1):
            e += 1
        while d < one.scaleb(e):
            e -= 1
    return e


def ref_round(magnitude, exact, spec):
    """用 Decimal 高精度舍入，返回 (数字串, 指数)；并用精确有理数校验。"""
    if spec.precision_mode is PrecisionMode.DECIMAL_PLACES:
        exp = -spec.precision
    elif magnitude == 0:
        exp = -(spec.precision - 1)
    else:
        exp = ref_decimal_exponent(magnitude) - spec.precision + 1
    if magnitude == 0:
        return "0", exp
    with localcontext() as ctx:
        ctx.prec = max(
            600,
            len(magnitude.as_tuple().digits)
            + abs(magnitude.as_tuple().exponent)
            + abs(exp)
            + 80,
        )
        mode = (
            ROUND_HALF_UP
            if spec.rounding is RoundingMode.HALF_UP
            else ROUND_HALF_EVEN
        )
        q = magnitude.quantize(Decimal(1).scaleb(exp), rounding=mode)
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


def ref_format(negative, magnitude, exact, spec, loc):
    digits, exp = ref_round(magnitude, exact, spec)
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

KIND_NAMES = ("十进制字符串", "float", "int", "Fraction", "精确半值", "Decimal")


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
        return s, KIND_NAMES[0]
    if kind == 1:  # 随机 float（含次正规数、极值、负零）
        special = [0.0, -0.0, 5e-324, 1e-300, 2.675, 0.1,
                   1.7976931348623157e308, -1.5, 9999.5]
        if rng.random() < 0.3:
            return rng.choice(special), KIND_NAMES[1]
        return rng.uniform(-1, 1) * 10.0 ** rng.randrange(-30, 31), KIND_NAMES[1]
    if kind == 2:  # 大整数
        return rng.randrange(-10 ** 40, 10 ** 40), KIND_NAMES[2]
    if kind == 3:  # 有理数（含非循环/循环分母）
        den = rng.choice([3, 6, 7, 9, 11, 13, 97, 10 ** rng.randrange(1, 20),
                          2 ** rng.randrange(1, 40), 7 * 10 ** 10])
        return Fraction(rng.randrange(-10 ** 25, 10 ** 25), den), KIND_NAMES[3]
    if kind == 4:  # 恰好半值构造：整数 + 0.5 个单位
        p = rng.randrange(0, 25)
        base = rng.randrange(0, 10 ** 12)
        return Fraction(2 * base + 1, 2 * 10 ** p), KIND_NAMES[4]
    # Decimal
    value = Decimal(rng.randrange(-10 ** 20, 10 ** 20)) / Decimal(10 ** rng.randrange(0, 15))
    return value, KIND_NAMES[5]


def random_spec(rng):
    mode = rng.choice([PrecisionMode.DECIMAL_PLACES, PrecisionMode.SIGNIFICANT])
    precision = (
        rng.randrange(0, 26)
        if mode is PrecisionMode.DECIMAL_PLACES
        else rng.randrange(1, 26)
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
    """返回 (locale, 来源)；来源为 'random' 或命中的语言配置名称。"""
    if rng.random() < 0.5:
        name = rng.choice(list(locales))
        return locales[name], name
    decimal_sep = rng.choice([".", ",", "·"])
    group_sep = rng.choice([s for s in [",", ".", " ", "'", "_"] if s != decimal_sep])
    return LocaleConfig(
        decimal_sep=decimal_sep,
        group_sep=group_sep,
        group_width=rng.choice([(3,), (3, 2), (2,), (4,), (3, 2, 2)]),
        negative_sign=rng.choice(["-", "−"]),
        positive_sign=rng.choice(["", "+"]),
        negative_parens=rng.random() < 0.2,
        currency_symbol=rng.choice(["", "$", "€", "¥"]),
        currency_position=rng.choice(["prefix", "suffix"]),
        currency_space=rng.random() < 0.5,
    ), "random"


def _fmt_width(width):
    return "(" + ", ".join(str(w) for w in width) + ("," if len(width) == 1 else "") + ")"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    rng = random.Random(20260927)
    locales = load_locales("locales.json")
    mismatches = 0

    seen_kinds = set()
    seen_modes = set()
    seen_roundings = set()
    seen_widths = set()
    seen_named = set()
    saw_sci = False
    saw_parens = False
    saw_currency = False
    saw_positive = False

    for _ in range(n):
        value, kind = random_value(rng)
        spec = random_spec(rng)
        loc, loc_source = random_locale(rng, locales)

        got = format_number(value, spec, loc)
        negative, magnitude, exact = ref_parse(value)
        want = ref_format(negative, magnitude, exact, spec, loc)

        seen_kinds.add(kind)
        seen_modes.add(spec.precision_mode.value)
        seen_roundings.add(spec.rounding.value)
        seen_widths.add(loc.group_width)
        if loc_source != "random":
            seen_named.add(loc_source)
        saw_sci = saw_sci or (spec.sci_high is not None or spec.sci_low is not None)
        saw_parens = saw_parens or loc.negative_parens
        saw_currency = saw_currency or bool(loc.currency_symbol)
        saw_positive = saw_positive or bool(loc.positive_sign)

        if got != want:
            mismatches += 1
            print(f"[不一致 #{mismatches}] value={value!r} ({kind})")
            print(f"  spec={spec}")
            print(f"  locale={loc}")
            print(f"  numfmt={got!r}  ref={want!r}")
            if mismatches >= 10:
                break
    if mismatches:
        print(f"失败：{n} 例中发现 {mismatches} 例不一致")
        sys.exit(1)

    print(f"通过：{n} 个随机用例（多语言/多精度/多舍入/科学计数法阈值）全部一致")
    print("覆盖统计（由本次运行实测，非手写）：")
    print(f"  输入类型 {len(seen_kinds)}/6：" + "、".join(sorted(seen_kinds)))
    print(f"  精度模式 {len(seen_modes)}/2：" + "、".join(sorted(seen_modes)))
    print(f"  舍入规则 {len(seen_roundings)}/2：" + "、".join(sorted(seen_roundings)))
    print(f"  分组宽度 {len(seen_widths)} 种："
          + "、".join(_fmt_width(w) for w in sorted(seen_widths)))
    print(f"  语言配置 {len(seen_named)}/{len(locales)} 套（locales.json）："
          + "、".join(sorted(seen_named)) + "；另有随机组合配置")
    print(f"  科学计数法阈值: {'覆盖' if saw_sci else '未覆盖'}"
          f"；括号负数: {'覆盖' if saw_parens else '未覆盖'}"
          f"；货币: {'覆盖' if saw_currency else '未覆盖'}"
          f"；显式正号: {'覆盖' if saw_positive else '未覆盖'}")

    if n >= 10000:
        expected_widths = {(3,), (3, 2), (2,), (4,), (3, 2, 2)}
        assert len(seen_kinds) == 6, f"输入类型未全覆盖: {seen_kinds}"
        assert len(seen_modes) == 2 and len(seen_roundings) == 2
        assert seen_named == set(locales), f"语言配置未全覆盖: {set(locales) - seen_named}"
        locale_widths = {tuple(loc.group_width) for loc in locales.values()}
        assert expected_widths | locale_widths <= seen_widths, (
            f"分组宽度未全覆盖: {_fmt_widths(sorted((expected_widths | locale_widths) - seen_widths))}"
        )


def _fmt_widths(items):
    return ", ".join(_fmt_width(w) for w in items)


if __name__ == "__main__":
    main()
