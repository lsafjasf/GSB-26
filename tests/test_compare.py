import unittest

from tplcheck import compare


def kinds(report, lang=None):
    return [
        d.kind for d in report.diagnostics if lang is None or d.lang == lang
    ]


class TestCompare(unittest.TestCase):
    def test_identical_signatures_ok(self):
        report = compare(
            {
                "en": "Hello {name:str}, {count:int} cats",
                "zh": "你好 {name:str}，有 {count:int} 只猫",
                "ja": "{name:str} さん、{count:int} 匹",
            }
        )
        self.assertTrue(report.ok, report.format())
        self.assertEqual(report.diagnostics, [])

    def test_missing_placeholder(self):
        report = compare({"en": "{a} {b}", "zh": "{a}"})
        self.assertFalse(report.ok)
        self.assertIn("MISSING_PLACEHOLDER", kinds(report, "zh"))

    def test_extra_placeholder(self):
        report = compare({"en": "{a}", "zh": "{a} {b}"})
        self.assertFalse(report.ok)
        self.assertIn("EXTRA_PLACEHOLDER", kinds(report, "zh"))

    def test_order_mismatch(self):
        report = compare({"en": "{a} {b} {c}", "zh": "{c} {a} {b}"})
        self.assertFalse(report.ok)
        self.assertIn("ORDER_MISMATCH", kinds(report, "zh"))

    def test_order_check_can_be_disabled(self):
        report = compare(
            {"en": "{a} {b}", "zh": "{b} {a}"}, check_order=False
        )
        self.assertTrue(report.ok, report.format())

    def test_type_conflict(self):
        report = compare({"en": "{n:int}", "zh": "{n:str}"})
        self.assertFalse(report.ok)
        self.assertIn("TYPE_CONFLICT", kinds(report, "zh"))

    def test_optional_conflict(self):
        report = compare({"en": "{n:int?}", "zh": "{n:int}"})
        self.assertFalse(report.ok)
        self.assertIn("OPTIONAL_CONFLICT", kinds(report, "zh"))

    def test_multiple_issues_reported_together(self):
        report = compare(
            {
                "en": "{a:int} {b:str} {c} {d}",
                "zh": "{b:str} {a:str} {e}",
            }
        )
        ks = kinds(report, "zh")
        self.assertIn("MISSING_PLACEHOLDER", ks)  # c, d
        self.assertIn("EXTRA_PLACEHOLDER", ks)  # e
        self.assertIn("TYPE_CONFLICT", ks)  # a
        self.assertIn("ORDER_MISMATCH", ks)  # b before a

    def test_per_template_validation_errors_surface(self):
        report = compare({"en": "{a}", "zh": "{#each xs as x}{x}{/each}{x}"})
        self.assertFalse(report.ok)
        self.assertIn("OUT_OF_SCOPE", kinds(report, "zh"))

    def test_syntax_error_reported(self):
        report = compare({"en": "{a}", "zh": "{#if x}"})
        self.assertFalse(report.ok)
        self.assertIn("SYNTAX_ERROR", kinds(report, "zh"))

    def test_sections_compared_by_signature(self):
        report = compare(
            {
                "en": "{#each users as u}{uname:str} {uage:int}{/each}",
                "zh": "{#each users as u}{uname:str} {uage:int}{/each}",
            }
        )
        self.assertTrue(report.ok, report.format())

    def test_empty_templates_equal(self):
        report = compare({"en": "", "zh": ""})
        self.assertTrue(report.ok, report.format())


if __name__ == "__main__":
    unittest.main()
