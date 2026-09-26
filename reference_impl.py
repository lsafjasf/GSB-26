"""reference_impl — 基于 fractions.Fraction 的独立参考实现。

与 numfmt 的关系：算法完全独立（Fraction 分子分母运算 + 余数与 1/2 的
精确比较），用于对拍验证 numfmt 的 Decimal 实现。不 import numfmt。
"""
from __future__ import annotations

from fractions import Fraction


# ---------------------------------------------------------------- 解析

def parse_value(s: str) -> tuple[bool, Fraction]:
    """十进制字符串 -> (是否负, 绝对值 Fraction)。支持科学计数法输入。"""
    s = s.strip().replace("_", "")
    neg = s.startswith("-")
    if s[:1] in "+-":
        s = s[1:]
    mant, _, exp_s = s.lower().partition("e")
    exp = int(exp_s) if exp_s else 0
    ip, _, fp = mant.partition(".")
    digits = (ip or "0") + fp
    scale = exp - len(fp)
    num = int(digits) if digits else 0
    val = Fraction(num * 10 ** scale, 1) if scale >= 0 else Fraction(num, 10 ** (-scale))
    return neg, val


# ---------------------------------------------------------------- 精确舍入

def _round_to_unit(av: Fraction, unit_exp: int, rounding: str) -> Fraction:
    """把非负 Fraction 舍入到 10**unit_exp 的整数倍。半值用余数精确比较。"""
    scaled = av * 10 ** (-unit_exp) if unit_exp < 0 else av / 10 ** unit_exp
    floor = scaled.numerator // scaled.denominator
    rem = scaled - floor
    half = Fraction(1, 2)
    if rem > half or (rem == half and (rounding == "half_up" or floor % 2 == 1)):
        floor += 1
    return Fraction(floor * 10 ** unit_exp, 1) if unit_exp >= 0 \
        else Fraction(floor, 10 ** (-unit_exp))


def _adjusted_exp(av: Fraction) -> int:
    """正 Fraction 的调整指数：10**e <= av < 10**(e+1)。"""
    n, d = av.numerator, av.denominator
    e = len(str(n)) - len(str(d))
    # 候选 e 附近精确校正
    def ge_pow(k):  # av >= 10**k ?
        return n >= d * 10 ** k if k >= 0 else n * 10 ** (-k) >= d
    while not ge_pow(e):
        e -= 1
    while ge_pow(e + 1):
        e += 1
    return e


def round_fraction(neg: bool, av: Fraction, spec: dict) -> tuple[bool, Fraction, int]:
    """返回 (输出是否带负号, 舍入后的绝对值 Fraction, 舍入单位指数)。"""
    if spec["mode"] == "places":
        unit_exp = -spec["places"]
    else:  # significant
        unit_exp = 0 if av == 0 else _adjusted_exp(av) - spec["sig_digits"] + 1
        if av == 0:
            unit_exp = -(spec["sig_digits"] - 1)
    r = _round_to_unit(av, unit_exp, spec["rounding"])
    if r == 0 and not spec["keep_negative_zero"]:
        neg = False
    return neg, r, unit_exp


# ---------------------------------------------------------------- 渲染

def _decimal_parts(av: Fraction, frac_digits: int) -> tuple[str, str]:
    """非负 Fraction（10**-unit_exp 的整数倍）-> (整数串, 小数串)。

    小数位数固定为 frac_digits（含尾部零），与舍入单位对齐。"""
    k = max(frac_digits, 0)
    scaled = av.numerator * 10 ** k
    q, rem = divmod(scaled, av.denominator)
    assert rem == 0, "Fraction 必须是 10 的幂单位的整数倍"
    s = str(q).rjust(k + 1, "0")
    return (s[:-k] if k else s), (s[-k:] if k else "")


def _group(int_str: str, sep: str, width) -> str:
    widths = [width] if isinstance(width, int) else list(width)
    out, i, k = [], len(int_str), 0
    while i > 0:
        w = widths[min(k, len(widths) - 1)]
        out.append(int_str[max(0, i - w):i])
        i -= w
        k += 1
    return sep.join(reversed(out))


def format_reference(value: str, spec: dict, locale: dict) -> str:
    neg, av = parse_value(value)
    neg, r, unit_exp = round_fraction(neg, av, spec)

    adj = _adjusted_exp(r) if r else None
    use_sci = r != 0 and (
        (spec.get("sci_high") is not None and adj >= spec["sci_high"]) or
        (spec.get("sci_low") is not None and adj <= spec["sci_low"]))

    if use_sci:
        md = spec.get("sci_mantissa_digits")
        if md is not None:
            neg, r, unit_exp = round_fraction(
                neg, r, {**spec, "mode": "significant", "sig_digits": md})
            adj = _adjusted_exp(r) if r else 0
        # 尾数：r / 10**adj，保留到舍入单位对应的位数（含尾部零），
        # 保证科学计数法字符串解析回去与舍入值严格相等。
        unit = Fraction(10 ** adj, 1) if adj >= 0 else Fraction(1, 10 ** (-adj))
        mant = r / unit
        ip, fp = _decimal_parts(mant, max(adj - unit_exp, 0))
        body = ip + (locale["decimal_sep"] + fp if fp else "") + f"e{adj}"
    else:
        # 小数位数与舍入单位对齐（places 模式 = places；
        # significant 模式 = 舍入单位指数对应的位数，零值时为 sig_digits-1）
        ip, fp = _decimal_parts(r, max(-unit_exp, 0))
        if spec["group"]:
            ip = _group(ip, locale["group_sep"], locale["group_width"])
        body = ip + (locale["decimal_sep"] + fp if fp else "")

    cur = spec.get("currency")
    if cur:
        gap = " " if cur.get("space") else ""
        body = (cur["symbol"] + gap + body) if cur.get("position", "prefix") == "prefix" \
            else (body + gap + cur["symbol"])

    if neg:
        return f"({body})" if spec["negative_style"] == "parens" \
            else locale["minus_sign"] + body
    return locale["plus_sign"] + body if spec["show_plus"] else body
