"""numfmt — 数值本地化格式化库（仅标准库）。

设计要点：
- 所有舍入基于 decimal.Decimal，上下文精度按输入动态放大，
  半值（x.5）判定精确，不经任何浮点中间表示。
- 科学计数法在舍入之后按“调整指数”(adjusted exponent) 自动切换，
  切换前后字符串解析回的数值严格相等（语义一致）。
- 配置缺项由 normalize_locale / normalize_spec 用默认值补齐。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import (Decimal, InvalidOperation, ROUND_HALF_UP,
                     ROUND_HALF_EVEN, localcontext)
from typing import Optional, Sequence, Union

__all__ = [
    "Locale", "Currency", "FormatSpec",
    "normalize_locale", "normalize_spec",
    "round_value", "format_number", "adjusted_exponent",
]

_ROUNDING_MAP = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}


# ---------------------------------------------------------------- 配置模型

@dataclass(frozen=True)
class Locale:
    decimal_sep: str = "."
    group_sep: str = ","
    # 分组宽度：整数（如 3），或从右往左的宽度序列（如 [3, 2] 表示印式分组，
    # 序列最后一个宽度向左循环重复）。
    group_width: Union[int, Sequence[int]] = 3
    minus_sign: str = "-"
    plus_sign: str = "+"


@dataclass(frozen=True)
class Currency:
    symbol: str
    position: str = "prefix"   # "prefix" | "suffix"
    space: bool = False


@dataclass(frozen=True)
class FormatSpec:
    mode: str = "places"           # "places"（小数位）| "significant"（有效数字）
    places: int = 2
    sig_digits: int = 6
    rounding: str = "half_up"      # "half_up"（四舍五入）| "half_even"（银行家）
    group: bool = True
    show_plus: bool = False
    negative_style: str = "sign"   # "sign" | "parens"（会计括号）
    keep_negative_zero: bool = True
    sci_high: Optional[int] = None  # 调整指数 >= sci_high 时切科学计数法
    sci_low: Optional[int] = None   # 调整指数 <= sci_low  时切科学计数法
    sci_mantissa_digits: Optional[int] = None  # 科学计数法尾数有效位；None=保留舍入精度
    currency: Optional[Currency] = None


_DEFAULT_LOCALE = Locale()
_DEFAULT_SPEC = FormatSpec()


def normalize_locale(cfg: Optional[dict]) -> Locale:
    """把（可能缺项的）dict 补全为 Locale。"""
    cfg = dict(cfg or {})
    if "group_width" in cfg and isinstance(cfg["group_width"], list):
        cfg["group_width"] = tuple(cfg["group_width"])
    return Locale(**{**{f: getattr(_DEFAULT_LOCALE, f) for f in Locale.__dataclass_fields__},
                     **cfg})


def normalize_spec(cfg: Optional[dict]) -> FormatSpec:
    """把（可能缺项的）dict 补全为 FormatSpec；currency 支持 dict 形式。"""
    cfg = dict(cfg or {})
    if isinstance(cfg.get("currency"), dict):
        cfg["currency"] = Currency(**cfg["currency"])
    return FormatSpec(**{**{f: getattr(_DEFAULT_SPEC, f) for f in FormatSpec.__dataclass_fields__},
                         **cfg})


# ---------------------------------------------------------------- 输入解析

def _to_decimal(value) -> Decimal:
    """str/int/Decimal/Fraction -> Decimal。float 走 repr，避免二次猜测。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool 不是数值")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        s = value.strip().replace("_", "")
        if not s:
            raise ValueError("空数值字符串")
        try:
            return Decimal(s)  # 支持 "1.23e-7"、"-0.000" 等
        except InvalidOperation:
            raise ValueError(f"无法解析的数值字符串: {value!r}") from None
    # Fraction 等 Rational：用足够精度展开（调用方应优先传字符串）
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        with localcontext() as ctx:
            ctx.prec = 200
            return +Decimal(value.numerator) / Decimal(value.denominator)
    raise TypeError(f"不支持的数值类型: {type(value)!r}")


# ---------------------------------------------------------------- 精确舍入

def adjusted_exponent(d: Decimal) -> int:
    """非零值的调整指数（最高有效位的 10 的幂次）。"""
    return d.adjusted()


def _quantize(d: Decimal, quantum: Decimal, rounding: str) -> Decimal:
    """在足够大的精度上下文里 quantize，保证半值判定精确。"""
    tup = d.as_tuple()
    # 结果最多需要的位数：整数位数 + 小数保留位数 + 余量
    need = max(len(tup.digits), abs(tup.exponent)) + abs(quantum.as_tuple().exponent) \
        + max(adjusted_exponent(d.copy_abs()) if d else 0, 0) + 50
    with localcontext() as ctx:
        ctx.prec = max(need, 50)
        return d.quantize(quantum, rounding=_ROUNDING_MAP[rounding])


def round_value(value, spec: FormatSpec) -> Decimal:
    """按 spec 舍入，返回舍入后的 Decimal（保留负零的符号）。"""
    d = _to_decimal(value)
    if not d.is_finite():
        raise ValueError("不支持 NaN/Inf")
    if spec.mode == "places":
        quantum = Decimal(1).scaleb(-spec.places)
    elif spec.mode == "significant":
        if spec.sig_digits < 1:
            raise ValueError("sig_digits 必须 >= 1")
        if d.is_zero():
            quantum = Decimal(1).scaleb(-(spec.sig_digits - 1))
        else:
            quantum = Decimal(1).scaleb(adjusted_exponent(d.copy_abs()) - spec.sig_digits + 1)
    else:
        raise ValueError(f"未知 mode: {spec.mode!r}")
    return _quantize(d, quantum, spec.rounding)


# ---------------------------------------------------------------- 渲染

def _group_integer(int_str: str, sep: str, width) -> str:
    widths = [width] if isinstance(width, int) else list(width)
    if not widths or any(w <= 0 for w in widths):
        raise ValueError("group_width 必须为正整数或正整数序列")
    parts, i, k = [], len(int_str), 0
    while i > 0:
        w = widths[min(k, len(widths) - 1)]
        j = max(0, i - w)
        parts.append(int_str[j:i])
        i, k = j, k + 1
    return sep.join(reversed(parts))


def _split_parts(d: Decimal):
    """Decimal -> (负号?, 整数字符串, 小数字符串)。"""
    tup = d.as_tuple()
    digits = "".join(map(str, tup.digits)) or "0"
    exp = tup.exponent
    if exp >= 0:
        int_part, frac_part = digits + "0" * exp, ""
    else:
        point = len(digits) + exp
        if point > 0:
            int_part, frac_part = digits[:point], digits[point:]
        else:
            int_part, frac_part = "0", "0" * (-point) + digits
    return bool(tup.sign), int_part, frac_part


def _render_plain(d: Decimal, spec: FormatSpec, loc: Locale) -> str:
    neg, int_part, frac_part = _split_parts(d)
    if spec.group:
        int_part = _group_integer(int_part, loc.group_sep, loc.group_width)
    body = int_part + (loc.decimal_sep + frac_part if frac_part else "")
    return body, neg


def _render_sci(d: Decimal, spec: FormatSpec, loc: Locale) -> str:
    """科学计数法：尾数保留舍入后的全部有效数字，保证解析回原值。"""
    if spec.sci_mantissa_digits is not None:
        sig_spec = FormatSpec(mode="significant", sig_digits=spec.sci_mantissa_digits,
                              rounding=spec.rounding)
        d = round_value(d, sig_spec)
    tup = d.as_tuple()
    digits = "".join(map(str, tup.digits)) or "0"
    exp = d.adjusted()
    mantissa = digits[0] + (loc.decimal_sep + digits[1:] if len(digits) > 1 else "")
    return f"{mantissa}e{exp}", bool(tup.sign)


def _use_sci(d: Decimal, spec: FormatSpec) -> bool:
    if d.is_zero():
        return False
    adj = d.adjusted()
    if spec.sci_high is not None and adj >= spec.sci_high:
        return True
    if spec.sci_low is not None and adj <= spec.sci_low:
        return True
    return False


def format_number(value, spec: Union[FormatSpec, dict, None] = None,
                  locale: Union[Locale, dict, None] = None) -> str:
    """主入口：舍入 -> 普通/科学计数法渲染 -> 符号/货币包装。"""
    spec = spec if isinstance(spec, FormatSpec) else normalize_spec(spec)
    loc = locale if isinstance(locale, Locale) else normalize_locale(locale)

    rounded = round_value(value, spec)
    is_zero = rounded.is_zero()
    neg = rounded.is_signed() and (not is_zero or spec.keep_negative_zero)

    if _use_sci(rounded, spec):
        body, _ = _render_sci(rounded, spec, loc)
    else:
        body, _ = _render_plain(rounded, spec, loc)

    # 货币
    cur = spec.currency
    if cur is not None:
        gap = " " if cur.space else ""
        body = f"{cur.symbol}{gap}{body}" if cur.position == "prefix" \
            else f"{body}{gap}{cur.symbol}"

    # 符号
    if neg:
        if spec.negative_style == "parens":
            return f"({body})"
        return f"{loc.minus_sign}{body}"
    if spec.show_plus:
        return f"{loc.plus_sign}{body}"
    return body
