"""numfmt 单元测试：python3 -m unittest test_numfmt -v"""
import json
import unittest
from fractions import Fraction

import numfmt
from numfmt import format_number, normalize_spec, normalize_locale, round_value

CFG = json.load(open("locales.json", encoding="utf-8"))
LOCALES = CFG["locales"]
SPECS = CFG["specs"]


class TestGroupingAndLocale(unittest.TestCase):
    def test_en_us(self):
        self.assertEqual(format_number("1234567.891", SPECS["plain2"], LOCALES["en-US"]),
                         "1,234,567.89")

    def test_de_de(self):
        self.assertEqual(format_number("1234567.891", SPECS["plain2"], LOCALES["de-DE"]),
                         "1.234.567,89")

    def test_fr_fr_narrow_space(self):
        self.assertEqual(format_number("1234567.891", SPECS["plain2"], LOCALES["fr-FR"]),
                         "1 234 567,89")

    def test_indian_grouping(self):
        self.assertEqual(format_number("12345678", SPECS["plain2"], LOCALES["hi-IN"]),
                         "1,23,45,678.00")

    def test_custom_width4(self):
        self.assertEqual(format_number("123456789.5", SPECS["plain2"], LOCALES["custom-4"]),
                         "1_2345_6789·50")

    def test_no_group(self):
        spec = {**SPECS["plain2"], "group": False}
        self.assertEqual(format_number("1234567.891", spec, LOCALES["en-US"]),
                         "1234567.89")


class TestRoundingExactness(unittest.TestCase):
    """半值判定必须精确：2.675 在浮点下是 2.67499...，十进制精确下是 2.68。"""

    def test_half_up_exact_tie(self):
        self.assertEqual(format_number("2.675", SPECS["plain2"]), "2.68")
        self.assertEqual(format_number("0.5", SPECS["banker0"].__class__ and
                                       {"places": 0, "rounding": "half_up"}), "1")

    def test_half_even_ties(self):
        spec = SPECS["banker0"]
        self.assertEqual(format_number("0.5", spec), "0")
        self.assertEqual(format_number("1.5", spec), "2")
        self.assertEqual(format_number("2.5", spec), "2")
        self.assertEqual(format_number("3.5", spec), "4")

    def test_half_even_long_tie(self):
        # 0.125 精确落在半值 -> 偶数 0.12；0.135 -> 0.14
        spec = {"places": 2, "rounding": "half_even"}
        self.assertEqual(format_number("0.125", spec), "0.12")
        self.assertEqual(format_number("0.135", spec), "0.14")

    def test_above_and_below_half_long_decimal(self):
        spec = {"places": 2, "rounding": "half_up"}
        self.assertEqual(format_number("0.12500000000000000000000001", spec), "0.13")
        self.assertEqual(format_number("0.12499999999999999999999999", spec), "0.12")

    def test_negative_half_up_away_from_zero(self):
        self.assertEqual(format_number("-2.5", {"places": 0, "rounding": "half_up"}), "-3")
        self.assertEqual(format_number("-2.5", SPECS["banker0"]), "-2")

    def test_significant_digits(self):
        spec = SPECS["sig6"]
        self.assertEqual(format_number("1234567.891", spec), "1,234,570")
        self.assertEqual(format_number("0.000123456789", spec), "0.000123457")
        self.assertEqual(format_number("999999.5", {**SPECS["sig6"], "sig_digits": 6}),
                         "1,000,000")


class TestSignAndCurrency(unittest.TestCase):
    def test_plus(self):
        spec = {**SPECS["plain2"], "show_plus": True}
        self.assertEqual(format_number("1234.5", spec), "+1,234.50")
        self.assertEqual(format_number("-1234.5", spec), "-1,234.50")

    def test_parens(self):
        self.assertEqual(format_number("-1234.5", SPECS["usd"]), "($1,234.50)")
        self.assertEqual(format_number("1234.5", SPECS["usd"]), "$1,234.50")

    def test_currency_suffix_with_space(self):
        self.assertEqual(format_number("1234.5", SPECS["eur"], LOCALES["de-DE"]),
                         "1.234,50 €")

    def test_currency_prefix_negative(self):
        self.assertEqual(format_number("-1234.5", SPECS["cny"]), "-¥1,234.50")


class TestZeroAndExtremes(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_number("0", SPECS["plain2"]), "0.00")
        self.assertEqual(format_number(0, SPECS["plain2"]), "0.00")

    def test_negative_zero_preserved(self):
        self.assertEqual(format_number("-0.000", SPECS["plain2"]), "-0.00")
        self.assertEqual(format_number("-0.000", SPECS["cny"]), "-¥0.00")

    def test_negative_zero_suppressed(self):
        spec = {**SPECS["plain2"], "keep_negative_zero": False}
        self.assertEqual(format_number("-0.000", spec), "0.00")

    def test_rounds_to_negative_zero(self):
        self.assertEqual(format_number("-0.0000001", SPECS["plain2"]), "-0.00")

    def test_tiny(self):
        self.assertEqual(format_number("1e-300", SPECS["plain2"]), "0.00")
        self.assertEqual(format_number("1e-300", {**SPECS["sig6"], "sci_low": -5}),
                         "1.00000e-300")

    def test_huge(self):
        self.assertEqual(format_number("1e300", SPECS["plain2"]),
                         "1" + ",000" * 100 + ".00")
        self.assertEqual(format_number("123e50", {**SPECS["sig6"], "sci_high": 7}),
                         "1.23000e52")

    def test_super_long_decimal(self):
        s = "1." + "3" * 500
        out = format_number(s, {"places": 10, "rounding": "half_up"})
        self.assertEqual(out, "1.3333333333")
        out = format_number("0." + "9" * 500, {"places": 10, "rounding": "half_up"})
        self.assertEqual(out, "1.0000000000")

    def test_carry_over_grouping_boundary(self):
        self.assertEqual(format_number("999999.999", SPECS["plain2"]), "1,000,000.00")


class TestScientificSwitch(unittest.TestCase):
    """自动切换阈值 + 切换前后语义一致（解析回 Fraction 严格相等）。"""

    def parse_back(self, s):
        from reference_impl import parse_value
        neg, v = parse_value(s.replace(",", ""))
        return -v if neg else v

    def test_boundary_plain_vs_sci(self):
        # sci_high=7：调整指数 6 普通、7 科学；sci_low=-5：-4 普通、-5 科学
        spec = {"places": 6, "rounding": "half_up", "sci_high": 7, "sci_low": -5}
        self.assertEqual(format_number("9999999.9999994", spec), "9,999,999.999999")
        self.assertEqual(format_number("9999999.9999995", spec), "1.0000000000000e7")
        self.assertEqual(format_number("0.0001", spec), "0.000100")
        self.assertEqual(format_number("0.00001", spec), "1.0e-5")
        self.assertEqual(format_number("0.000009", spec), "9e-6")

    def test_semantic_consistency_across_switch(self):
        spec = {"places": 6, "rounding": "half_up", "sci_high": 7, "sci_low": -5}
        for s in ["9999999.9999994", "9999999.9999995", "12345678.91",
                  "0.0001", "0.00001", "0.000009", "2.5e-8", "9.99e30"]:
            out = format_number(s, spec)
            rounded = round_value(s, normalize_spec(spec))
            self.assertEqual(self.parse_back(out), Fraction(rounded),
                             f"{s} -> {out}")

    def test_mantissa_digits_override(self):
        spec = {**SPECS["sci-auto"], "sci_mantissa_digits": 3}
        self.assertEqual(format_number("12345678.91", spec), "1.23e7")


class TestConfigDefaults(unittest.TestCase):
    def test_empty_configs(self):
        self.assertEqual(format_number("1234.5"), "1,234.50")
        self.assertEqual(format_number("1234.5", None, None), "1,234.50")

    def test_partial_configs(self):
        self.assertEqual(format_number("1234.5", {"places": 0}), "1,235")
        self.assertEqual(format_number("1234.5", {}, {"group_sep": " "}), "1 234.50")
        self.assertEqual(format_number("1234.5", {"currency": {"symbol": "£"}}),
                         "£1,234.50")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            format_number("abc")
        with self.assertRaises(ValueError):
            format_number("1", {"mode": "nope"})
        with self.assertRaises(ValueError):
            format_number("1", {"sig_digits": 0, "mode": "significant"})


if __name__ == "__main__":
    unittest.main()
