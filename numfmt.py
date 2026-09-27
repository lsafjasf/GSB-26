"""numfmt -- 数值本地化格式化库（仅依赖 Python 标准库）。

特性：
- 千分位分组，分组宽度可配置（支持印度式 (3, 2) 分组）
- 小数点 / 分组分隔符 / 正负号 / 货币位置按语言配置
- 舍入规则可配置：四舍五入 (HALF_UP) 与银行家舍入 (HALF_EVEN)，
  半值判定基于 fractions.Fraction 精确有理数运算，无浮点近似
- 科学计数法阈值自动切换，切换前后数值语义一致（同一舍入结果，仅记法不同）
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from fractions import Fraction

__all__ = [
    "RoundingMode",
    "PrecisionMode",
    "LocaleConfig",
    "FormatSpec",
    "format_number",
    "load_locales",
]


class RoundingMode(Enum):
    HALF_UP = "half_up"      # 四舍五入：恰好半值向远离零方向进位
    HALF_EVEN = "half_even"  # 银行家舍入：恰好半值向偶数进位


class PrecisionMode(Enum):
    DECIMAL_PLACES = "decimal_places"  # 按小数位
    SIGNIFICANT = "significant"        # 按有效数字


@dataclass(frozen=True)
class LocaleConfig:
    """语言 / 地区显示配置。所有字段均有默认值，允许缺项。"""

    decimal_sep: str = "."
    group_sep: str = ","
    group_width: tuple = (3,)       # 从右往左各组宽度，末项循环；(3, 2) 为印度式
    negative_sign: str = "-"
    positive_sign: str = ""         # 设为 "+" 则正数显式带号
    negative_parens: bool = False   # 会计风格：(1,234.56)
    currency_symbol: str = ""
    currency_position: str = "prefix"  # prefix | suffix
    currency_space: bool = False

    def __post_init__(self):
        object.__setattr__(self, "group_width", tuple(self.group_width))
        if not self.group_width or any(
            not isinstance(w, int) or w <= 0 for w in self.group_width
        ):
            raise ValueError("group_width 必须为正整数非空序列")
        if self.currency_position not in ("prefix", "suffix"):
            raise ValueError("currency_position 仅支持 prefix / suffix")
        if self.decimal_sep and self.decimal_sep == self.group_sep:
            raise ValueError("小数分隔符与分组分隔符不能相同")

    @classmethod
    def from_dict(cls, data):
        """由 dict 构造；缺项用默认值，未知键忽略。"""
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})

    def merged(self, **overrides):
        """返回覆盖指定项后的新配置（None 表示不覆盖）。"""
        return replace(self, **{k: v for k, v in overrides.items() if v is not None})


@dataclass(frozen=True)
class FormatSpec:
    """格式化规格：精度、舍入、科学计数法阈值等。所有字段均有默认值。"""

    precision_mode: PrecisionMode = PrecisionMode.DECIMAL_PLACES
    precision: int = 2
    rounding: RoundingMode = RoundingMode.HALF_UP
    sci_high: int | None = None   # 十进制指数 >= sci_high 时切换科学计数法
    sci_low: int | None = None    # 十进制指数 <= sci_low  时切换科学计数法
    group: bool = True
    keep_negative_zero: bool = True   # 负零（含舍入到零的负数）是否保留负号
    sci_marker: str = "e"

    def __post_init__(self):
        if isinstance(self.precision_mode, str):
            object.__setattr__(self, "precision_mode", PrecisionMode(self.precision_mode))
        if isinstance(self.rounding, str):
            object.__setattr__(self, "rounding", RoundingMode(self.rounding))
        if not isinstance(self.precision, int) or isinstance(self.precision, bool):
            raise ValueError("precision 必须为整数")
        if self.precision_mode is PrecisionMode.SIGNIFICANT and self.precision < 1:
            raise ValueError("有效数字精度必须 >= 1")
        if self.precision_mode is PrecisionMode.DECIMAL_PLACES and self.precision < 0:
            raise ValueError("小数位精度必须 >= 0")

    @classmethod
    def from_dict(cls, data):
        """由 dict 构造；缺项用默认值，未知键忽略，枚举可写字符串。"""
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})


# ---------------------------------------------------------------- 输入解析

_STR_NUM_RE = re.compile(r"^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$")


def _to_signed_fraction(value):
    """把输入精确转换为 (是否负, |值| 的 Fraction)。

    支持 int / float / str / Decimal / Fraction。float 按其二进制精确值转换；
    负零（-0.0、"-0"、Decimal("-0")）的符号被保留。
    """
    if isinstance(value, bool):
        raise TypeError("不支持 bool 类型")
    if isinstance(value, int):
        return value < 0, Fraction(abs(value))
    if isinstance(value, Fraction):
        return value < 0, abs(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("不支持 NaN / Inf")
        return math.copysign(1.0, value) < 0, Fraction(abs(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("不支持 NaN / Inf")
        return value.is_signed(), Fraction(abs(value))
    if isinstance(value, str):
        m = _STR_NUM_RE.match(value.strip())
        if not m or not (m.group(2) or m.group(3)):
            raise ValueError(f"无法解析的数值字符串: {value!r}")
        sign, int_part, frac_part, exp_part = m.groups()
        digits = (int_part or "") + (frac_part or "")
        frac = Fraction(int(digits)) if digits else Fraction(0)
        scale = len(frac_part or "") - int(exp_part or 0)
        if scale > 0:
            frac /= 10 ** scale
        elif scale < 0:
            frac *= 10 ** (-scale)
        return sign == "-", frac
    raise TypeError(f"不支持的类型: {type(value).__name__}")


# ---------------------------------------------------------------- 精确舍入

def _round_to_mantissa(frac, exp, mode):
    """把非负 Fraction 舍入为 m * 10**exp，返回整数 m。

    半值判定用 2*r 与分母比较，完全精确，无任何浮点近似。
    """
    if exp >= 0:
        scaled = frac / Fraction(10 ** exp)
    else:
        scaled = frac * Fraction(10 ** (-exp))
    q, r = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * r
    if twice > scaled.denominator or (
        twice == scaled.denominator
        and (mode is RoundingMode.HALF_UP or q % 2 == 1)
    ):
        q += 1
    return q


def _decimal_exponent(frac):
    """返回 e 使 10**e <= frac < 10**(e+1)（frac > 0），纯整数运算。"""
    n, d = frac.numerator, frac.denominator
    e = len(str(n)) - len(str(d))

    def ge_pow(k):  # frac >= 10**k ?
        if k >= 0:
            return n >= d * 10 ** k
        return n * 10 ** (-k) >= d

    if ge_pow(e + 1):
        return e + 1
    if not ge_pow(e):
        return e - 1
    return e


def _round_value(frac, spec):
    """把非负 Fraction 按 spec 舍入，返回 (尾数 m, 指数 exp)，值 = m * 10**exp。"""
    if frac == 0:
        if spec.precision_mode is PrecisionMode.DECIMAL_PLACES:
            return 0, -spec.precision
        return 0, -(spec.precision - 1)
    if spec.precision_mode is PrecisionMode.DECIMAL_PLACES:
        exp = -spec.precision
        return _round_to_mantissa(frac, exp, spec.rounding), exp
    k = spec.precision
    exp = _decimal_exponent(frac) - k + 1
    m = _round_to_mantissa(frac, exp, spec.rounding)
    if m >= 10 ** k:  # 进位多出一位，如 999.5 -> 1000（4 位有效数字）
        m //= 10
        exp += 1
    return m, exp


# ---------------------------------------------------------------- 排版输出

def _group_digits(int_part, locale):
    widths = locale.group_width
    groups = []
    i = len(int_part)
    level = 0
    while i > 0:
        width = widths[min(level, len(widths) - 1)]
        j = max(0, i - width)
        groups.append(int_part[j:i])
        i = j
        level += 1
    return locale.group_sep.join(reversed(groups))


def _format_fixed(m, exp, spec, locale):
    digits = str(m)
    point = len(digits) + exp
    if point <= 0:
        int_part, frac_part = "0", "0" * (-point) + digits
    elif point >= len(digits):
        int_part, frac_part = digits + "0" * (point - len(digits)), ""
    else:
        int_part, frac_part = digits[:point], digits[point:]
    if spec.group and locale.group_sep:
        int_part = _group_digits(int_part, locale)
    if frac_part:
        return int_part + locale.decimal_sep + frac_part
    return int_part


def _format_sci(m, exp, spec, locale):
    digits = str(m)
    e = exp + len(digits) - 1
    mantissa = digits[0]
    if len(digits) > 1:
        mantissa += locale.decimal_sep + digits[1:]
    sign = "+" if e >= 0 else "-"
    return f"{mantissa}{spec.sci_marker}{sign}{abs(e)}"


def _use_sci(m, exp, spec):
    if m == 0:
        return False
    e = exp + len(str(m)) - 1
    if spec.sci_high is not None and e >= spec.sci_high:
        return True
    if spec.sci_low is not None and e <= spec.sci_low:
        return True
    return False


# ---------------------------------------------------------------- 入口

def format_number(value, spec=None, locale=None):
    """把 value 按 spec（精度/舍入/科学计数法）与 locale（语言习惯）格式化。

    value 可为 int / float / str / Decimal / Fraction；
    spec 为 FormatSpec 或 dict；locale 为 LocaleConfig 或 dict；均可省略。
    """
    if spec is None:
        spec = FormatSpec()
    elif isinstance(spec, dict):
        spec = FormatSpec.from_dict(spec)
    if locale is None:
        locale = LocaleConfig()
    elif isinstance(locale, dict):
        locale = LocaleConfig.from_dict(locale)

    negative, frac = _to_signed_fraction(value)
    m, exp = _round_value(frac, spec)
    if m == 0 and not spec.keep_negative_zero:
        negative = False

    if _use_sci(m, exp, spec):
        body = _format_sci(m, exp, spec, locale)
    else:
        body = _format_fixed(m, exp, spec, locale)

    if locale.currency_symbol:
        gap = " " if locale.currency_space else ""
        if locale.currency_position == "prefix":
            body = locale.currency_symbol + gap + body
        else:
            body = body + gap + locale.currency_symbol

    if negative:
        if locale.negative_parens:
            return "(" + body + ")"
        return locale.negative_sign + body
    return locale.positive_sign + body


def load_locales(path):
    """从 JSON 文件加载语言配置表，返回 {名称: LocaleConfig}。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("语言配置文件必须是 JSON 对象")
    return {name: LocaleConfig.from_dict(cfg) for name, cfg in data.items()}
