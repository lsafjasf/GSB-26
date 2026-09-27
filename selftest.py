"""numfmt 单元自测：零、负零、极小/极大值、超长小数、配置缺项、
科学计数法自动切换边界、精确半值舍入、多语言排版。

运行：python3 selftest.py
"""
import unittest
from decimal import Decimal
from fractions import Fraction

from numfmt import (
    FormatSpec,
    LocaleConfig,
    PrecisionMode,
    RoundingMode,
    format_number,
    load_locales,
)

LOCALES = load_locales("locales.json")


def sig(p, **kw):
    return FormatSpec(precision_mode="significant", precision=p, **kw)


def parse_back(text, locale):
    """把格式化结果解析回 Fraction，用于验证数值语义一致。"""
    s = text
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if locale.currency_symbol:
        s = s.replace(locale.currency_symbol, "").strip()
    if locale.negative_sign and s.startswith(locale.negative_sign):
        negative = True
        s = s[len(locale.negative_sign):]
    elif locale.positive_sign and s.startswith(locale.positive_sign):
        s = s[len(locale.positive_sign):]
    if locale.group_sep:
        s = s.replace(locale.group_sep, "")
    if locale.decimal_sep != ".":
        s = s.replace(locale.decimal_sep, ".")
    value = Fraction(Decimal(s))
    return -value if negative else value


class TestRoundingExactness(unittest.TestCase):
    def test_half_up_exact_tie(self):
        spec = FormatSpec(precision=2)
        self.assertEqual(format_number("0.125", spec), "0.13")
        self.assertEqual(format_number("2.675", spec), "2.68")
        self.assertEqual(format_number("0.005", spec), "0.01")

    def test_half_even_exact_tie(self):
        spec = FormatSpec(precision=2, rounding="half_even")
        self.assertEqual(format_number("0.125", spec), "0.12")  # 2 偶，舍
        self.assertEqual(format_number("0.375", spec), "0.38")  # 7 奇，入
        self.assertEqual(format_number("2.685", spec), "2.68")
        self.assertEqual(format_number("2.695", spec), "2.70")

    def test_float_binary_value_is_exact(self):
        # 2.675 的二进制精确值是 2.67499999...，必须舍向 2.67 而非 2.68
        spec = FormatSpec(precision=2)
        self.assertEqual(format_number(2.675, spec), "2.67")
        self.assertEqual(format_number("2.675", spec), "2.68")

    def test_tie_with_long_tail_is_not_half(self):
        # 0.125000...0001 不是半值，两种舍入都必须进位
        v = "0.125" + "0" * 500 + "1"
        for mode in ("half_up", "half_even"):
            spec = FormatSpec(precision=2, rounding=mode)
            self.assertEqual(format_number(v, spec), "0.13")
        # 0.125000...000 恰好是半值
        v2 = "0.125" + "0" * 500
        self.assertEqual(format_number(v2, FormatSpec(precision=2)), "0.13")
        self.assertEqual(
            format_number(v2, FormatSpec(precision=2, rounding="half_even")), "0.12"
        )

    def test_significant_rounding_carry(self):
        self.assertEqual(format_number("999.5", sig(3)), "1,000")
        self.assertEqual(format_number("999.4", sig(3)), "999")
        self.assertEqual(format_number("0.0009999", sig(2)), "0.0010")


class TestZeroAndNegativeZero(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_number(0), "0.00")
        self.assertEqual(format_number("0.0000"), "0.00")
        self.assertEqual(format_number(0, sig(4)), "0.000")

    def test_negative_zero_kept_by_default(self):
        self.assertEqual(format_number(-0.0), "-0.00")
        self.assertEqual(format_number("-0.000"), "-0.00")
        self.assertEqual(format_number(Decimal("-0")), "-0.00")

    def test_negative_zero_stripped_on_demand(self):
        spec = FormatSpec(keep_negative_zero=False)
        self.assertEqual(format_number(-0.0, spec), "0.00")

    def test_negative_rounding_to_zero(self):
        self.assertEqual(format_number("-0.0001"), "-0.00")
        spec = FormatSpec(keep_negative_zero=False)
        self.assertEqual(format_number("-0.0001", spec), "0.00")


class TestExtremeValues(unittest.TestCase):
    def test_tiny_subnormal(self):
        spec = sig(3, sci_low=-5)
        self.assertEqual(format_number(5e-324, spec), "4.94e-324")

    def test_huge_float(self):
        spec = sig(4, sci_high=6)
        out = format_number(1.7976931348623157e308, spec)
        self.assertEqual(out, "1.798e+308")

    def test_huge_int(self):
        spec = FormatSpec(precision=2)
        out = format_number(10 ** 300, spec)
        self.assertEqual(out, "1" + ",000" * 100 + ".00")
        self.assertEqual(parse_back(out, LocaleConfig()), Fraction(10 ** 300))

    def test_long_decimal_string(self):
        v = "0." + "3" * 1000
        self.assertEqual(format_number(v, sig(6)), "0.333333")
        self.assertEqual(format_number(v, FormatSpec(precision=2)), "0.33")
        v2 = "1." + "0" * 500 + "5"
        self.assertEqual(format_number(v2, FormatSpec(precision=3)), "1.000")

    def test_string_exponent_input(self):
        self.assertEqual(format_number("1.23e5"), "123,000.00")
        self.assertEqual(format_number("1e-7"), "0.00")
        self.assertEqual(format_number("-2.5E3", sig(2)), "-2,500")

    def test_fraction_input(self):
        self.assertEqual(format_number(Fraction(1, 3), FormatSpec(precision=4)), "0.3333")
        self.assertEqual(format_number(Fraction(-22, 7), sig(4)), "-3.143")


class TestSciThresholdBoundary(unittest.TestCase):
    """自动切换边界数据：切换前后必须是同一舍入值的不同记法。"""

    SPEC = sig(4, sci_high=4, sci_low=-3)

    BOUNDARY = [
        # (输入, 期望输出, 舍入后的精确值)
        ("9999.4", "9,999", Fraction(9999)),          # E=3，定点
        ("9999.5", "1.000e+4", Fraction(10000)),      # 半值进位 -> E=4，切换
        ("9999.4999", "9,999", Fraction(9999)),
        ("9999.5001", "1.000e+4", Fraction(10000)),
        ("0.01", "0.01000", Fraction(1, 100)),        # E=-2，定点
        ("0.001", "1.000e-3", Fraction(1, 1000)),     # E=-3，切换
        ("0.00099994", "9.999e-4", Fraction(9999, 10000000)),  # E=-4，切换
        ("0.00099999", "1.000e-3", Fraction(1, 1000)),  # 舍入进位跨过阈值
        ("0", "0.000", Fraction(0)),                  # 零永不切换
    ]

    def test_boundary_outputs(self):
        for raw, expected, _ in self.BOUNDARY:
            with self.subTest(raw=raw):
                self.assertEqual(format_number(raw, self.SPEC), expected)

    def test_semantics_consistent_across_switch(self):
        loc = LocaleConfig()
        for raw, _, value in self.BOUNDARY:
            with self.subTest(raw=raw):
                self.assertEqual(parse_back(format_number(raw, self.SPEC), loc), value)

    def test_half_even_at_switch_boundary(self):
        spec = sig(4, sci_high=4, rounding="half_even")
        self.assertEqual(format_number("9998.5", spec), "9,998")  # 9998 偶，不进位
        self.assertEqual(format_number("9999.5", spec), "1.000e+4")  # 9999 奇，进位


class TestLocales(unittest.TestCase):
    def test_grouping_widths(self):
        self.assertEqual(format_number("1234567.89"), "1,234,567.89")
        self.assertEqual(
            format_number("12345678.9", FormatSpec(precision=1), LOCALES["hi_IN"]),
            "₹1,23,45,678.9",
        )
        loc = LocaleConfig(group_width=(2,))
        self.assertEqual(format_number("123456", FormatSpec(precision=0), loc), "12,34,56")
        self.assertEqual(
            format_number("1234567.89", FormatSpec(precision=2, group=False)),
            "1234567.89",
        )

    def test_separators_per_locale(self):
        self.assertEqual(format_number("1234.56", locale=LOCALES["de_DE"]), "1.234,56 €")
        self.assertEqual(
            format_number("1234.5", locale=LOCALES["fr_FR"]), "1 234,50 €"
        )

    def test_currency_position(self):
        self.assertEqual(format_number("-1234.5", locale=LOCALES["en_US"]), "-$1,234.50")
        self.assertEqual(format_number("1234.5", locale=LOCALES["zh_CN"]), "¥1,234.50")
        self.assertEqual(format_number("1234.5", locale=LOCALES["de_DE"]), "1.234,50 €")

    def test_sign_rules(self):
        self.assertEqual(
            format_number("1234.5", locale=LOCALES["de_DE_signed"]), "+1.234,50"
        )
        self.assertEqual(
            format_number("-1234.5", locale=LOCALES["en_US_accounting"]), "($1,234.50)"
        )
        loc = LocaleConfig(positive_sign="+")
        self.assertEqual(format_number("1.5", locale=loc), "+1.50")


class TestMissingConfig(unittest.TestCase):
    def test_defaults_when_omitted(self):
        self.assertEqual(format_number("1234.5"), "1,234.50")
        self.assertEqual(LocaleConfig.from_dict({}), LocaleConfig())
        self.assertEqual(FormatSpec.from_dict({}), FormatSpec())

    def test_partial_config_fills_defaults(self):
        loc = LocaleConfig.from_dict({"decimal_sep": ",", "group_sep": ".", "unknown_key": 1})
        self.assertEqual(loc.decimal_sep, ",")
        self.assertEqual(loc.group_sep, ".")
        self.assertEqual(loc.group_width, (3,))
        spec = FormatSpec.from_dict({"precision": 4, "rounding": "half_even"})
        self.assertEqual(spec.precision, 4)
        self.assertEqual(spec.rounding, RoundingMode.HALF_EVEN)
        self.assertEqual(spec.precision_mode, PrecisionMode.DECIMAL_PLACES)

    def test_dict_arguments(self):
        self.assertEqual(
            format_number("1234.5", {"precision": 1}, {"decimal_sep": ",", "group_sep": "."}),
            "1.234,5",
        )

    def test_merged(self):
        base = LOCALES["en_US"]
        loc = base.merged(currency_symbol="", positive_sign="+")
        self.assertEqual(loc.currency_symbol, "")
        self.assertEqual(loc.positive_sign, "+")
        self.assertEqual(loc.group_width, (3,))

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            LocaleConfig(group_width=(0,))
        with self.assertRaises(ValueError):
            LocaleConfig(decimal_sep=",", group_sep=",")
        with self.assertRaises(ValueError):
            FormatSpec(precision_mode="significant", precision=0)

    def test_invalid_values_rejected(self):
        for bad in ("abc", float("nan"), float("inf"), True, None):
            with self.assertRaises((ValueError, TypeError)):
                format_number(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
