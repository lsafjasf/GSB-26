"""Self-tests for csvcheck (standard library unittest only)."""

import json
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csvcheck import (CATEGORY_FIELD, CATEGORY_MALFORMED, Field, Validator)

SCHEMA = [
    Field("id", "int"),
    Field("name", "str", min_length=1, max_length=20),
    Field("email", "str", pattern=r"^[^@\s]+@[^@\s]+$", severity="warning"),
    Field("age", "int", min_value=0, max_value=150),
    Field("role", "enum", choices=["admin", "user", "guest"]),
]
HEADER = "id,name,email,age,role"


def write(tmp, name, content, mode="w"):
    path = os.path.join(tmp, name)
    with open(path, mode) as fh:
        fh.write(content)
    return path


class CsvCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def validate(self, path, **kw):
        return Validator(SCHEMA, **kw).validate_file(path)

    # -- 1. empty file -------------------------------------------------------
    def test_empty_file(self):
        path = write(self.tmp, "empty.csv", "")
        result = self.validate(path)
        self.assertEqual(result.rows_processed, 0)
        self.assertEqual(result.total_errors, 0)
        self.assertFalse(result.truncated)
        self.assertEqual(result.errors, [])

    def test_header_only_file(self):
        path = write(self.tmp, "header.csv", HEADER + "\n")
        result = self.validate(path)
        self.assertEqual(result.total_errors, 0)

    # -- 2. file where every line is bad --------------------------------------
    def test_all_bad_lines(self):
        body = "".join(f"{i},,not-an-email,999,alien\n" for i in range(50))
        path = write(self.tmp, "allbad.csv", HEADER + "\n" + body)
        result = self.validate(path)
        self.assertEqual(result.rows_processed, 50)
        # each row: name too_short + email pattern + age range + role choice
        self.assertEqual(result.total_errors, 50 * 4)
        self.assertEqual(result.by_category[CATEGORY_FIELD], 200)
        self.assertNotIn(CATEGORY_MALFORMED, result.by_category)
        self.assertEqual(result.by_field["age"], 50)
        self.assertEqual(result.by_field["role"], 50)

    # -- 3. errors far beyond the limit -> truncated flag ----------------------
    def test_error_limit_truncation(self):
        body = "".join(f"{i},,bad,999,alien\n" for i in range(500))
        path = write(self.tmp, "flood.csv", HEADER + "\n" + body)
        result = self.validate(path, max_errors=10)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.errors), 10)          # collection stopped
        self.assertEqual(result.total_errors, 2000)       # true total kept
        # both output forms must flag truncation explicitly
        self.assertIn("结果被截断", result.to_text())
        self.assertTrue(json.loads(result.to_json())["truncated"])
        # summary counters still reflect ALL errors, not just collected ones
        self.assertEqual(result.by_field["age"], 500)

    def test_no_truncation_under_limit(self):
        path = write(self.tmp, "ok.csv", HEADER + "\n1,alice,a@b.c,30,admin\n")
        result = self.validate(path, max_errors=10)
        self.assertFalse(result.truncated)
        self.assertNotIn("结果被截断", result.to_text())

    # -- 4. malformed rows vs field-validation errors --------------------------
    def test_malformed_categories_and_snippets(self):
        lines = [
            HEADER,
            '1,"unclosed,a@b.c,30,admin',          # unclosed quote
            '2,bob,b@b.c,30',                       # too few fields
            '3,cara,c@c.d,30,admin,extra',          # too many fields
            '4,dave,d@d.e,40,user',                 # good row
        ]
        path = write(self.tmp, "malformed.csv", "\n".join(lines) + "\n")
        result = self.validate(path)
        codes = {e.code for e in result.errors}
        self.assertEqual(codes, {"unclosed_quote", "field_count_mismatch"})
        for e in result.errors:
            self.assertEqual(e.category, CATEGORY_MALFORMED)
            self.assertIsNotNone(e.raw_snippet)     # raw fragment preserved
            self.assertIsNone(e.field)              # no field for bad rows
        quote_err = next(e for e in result.errors if e.code == "unclosed_quote")
        self.assertEqual(quote_err.line, 2)
        self.assertEqual(quote_err.column, 3)       # offset of the quote char
        self.assertIn('"unclosed', quote_err.raw_snippet)
        self.assertEqual(result.by_category[CATEGORY_MALFORMED], 3)

    def test_encoding_error_is_malformed(self):
        path = write(self.tmp, "enc.csv",
                     HEADER + "\n1,alice,a@b.c,30,admin\n")
        with open(path, "ab") as fh:                # invalid utf-8 bytes
            fh.write(b"2,bob,b@b.c,30,\xff\xfeuser\n")
        result = self.validate(path)
        enc = [e for e in result.errors if e.code == "encoding_error"]
        self.assertEqual(len(enc), 1)
        self.assertEqual(enc[0].category, CATEGORY_MALFORMED)
        self.assertEqual(enc[0].severity, "critical")
        self.assertIsNotNone(enc[0].raw_snippet)

    # -- 5. error record completeness ------------------------------------------
    def test_error_record_fields(self):
        path = write(self.tmp, "one.csv",
                     HEADER + "\n7,,bad,200,alien\n")
        result = self.validate(path)
        age_err = next(e for e in result.errors if e.field == "age")
        self.assertEqual(age_err.file, path)
        self.assertEqual(age_err.line, 2)
        self.assertEqual(age_err.column, 4)
        self.assertEqual(age_err.expected, "<= 150")
        self.assertEqual(age_err.actual, "200")
        self.assertEqual(age_err.category, CATEGORY_FIELD)

    # -- 6. severity settings ----------------------------------------------------
    def test_min_severity_filter(self):
        path = write(self.tmp, "sev.csv",
                     HEADER + "\n1,alice,not-an-email,30,admin\n")
        # email violations are severity=warning
        result = self.validate(path)
        self.assertEqual(result.total_errors, 1)
        result = self.validate(path, min_severity="error")
        self.assertEqual(result.total_errors, 0)

    # -- 7. summary <-> details <-> text report consistency ----------------------
    def check_consistency(self, result):
        summary = json.loads(result.to_json())
        # summary counts == recomputed from collected details (untruncated)
        if not result.truncated:
            by_field, by_cat, by_sev = {}, {}, {}
            for e in result.errors:
                key = e.field if e.field is not None else "(row)"
                by_field[key] = by_field.get(key, 0) + 1
                by_cat[e.category] = by_cat.get(e.category, 0) + 1
                by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
            self.assertEqual(summary["by_field"], dict(sorted(by_field.items())))
            self.assertEqual(summary["by_category"], dict(sorted(by_cat.items())))
            self.assertEqual(summary["by_severity"], dict(sorted(by_sev.items())))
            self.assertEqual(summary["total_errors"], len(result.errors))
        # every grouped count appears verbatim in the text report
        text = result.to_text()
        for section in ("by_category", "by_code", "by_field", "by_severity"):
            for key, count in summary[section].items():
                self.assertIn(f"  {key}: {count}\n", text,
                              f"text report missing {section}/{key}")
        self.assertIn(f"Total errors: {summary['total_errors']}", text)
        # detail line count in text == collected errors
        detail_lines = [l for l in text.splitlines()
                        if re.match(r"  \S+:\d+", l)]
        self.assertEqual(len(detail_lines), summary["errors_collected"])
        # truncation flag consistent between the two forms
        self.assertEqual(summary["truncated"], "结果被截断" in text)

    def test_consistency_clean_and_dirty(self):
        lines = [HEADER, "1,alice,a@b.c,30,admin"]
        lines += [f"{i},,bad,999,alien" for i in range(20)]
        lines.append('2,"oops,x@y.z,10,user')
        path = write(self.tmp, "mix.csv", "\n".join(lines) + "\n")
        self.check_consistency(self.validate(path))                    # complete
        self.check_consistency(self.validate(path, max_errors=5))      # truncated

    # -- 8. performance smoke (full 1M-row benchmark lives in bench.py) ---------
    def test_performance_smoke(self):
        rows = [HEADER]
        for i in range(100_000):
            rows.append(f"{i},user{i},u{i}@x.y,25,user")
        path = write(self.tmp, "big.csv", "\n".join(rows) + "\n")
        start = time.perf_counter()
        result = self.validate(path)
        elapsed = time.perf_counter() - start
        self.assertEqual(result.total_errors, 0)
        self.assertEqual(result.rows_processed, 100_000)
        self.assertLess(elapsed, 20, f"too slow: {elapsed:.1f}s for 100k rows")
        print(f"\n[perf] 100k clean rows in {elapsed:.2f}s "
              f"({100_000 / elapsed:,.0f} rows/s)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
