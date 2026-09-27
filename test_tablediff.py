#!/usr/bin/env python3
"""单元自测：python3 -m unittest test_tablediff -v"""

import unittest

from tablediff import Column, DiffConfig, Table, diff_tables


def T(schema, rows):
    return Table(schema, rows)


class TestBasicDiff(unittest.TestCase):
    def setUp(self):
        self.schema = [Column("id", "int"), Column("name", "str"), Column("price", "float")]

    def test_identical(self):
        a = T(self.schema, [(1, "a", 1.0), (2, "b", 2.0)])
        b = T(self.schema, [(2, "b", 2.0), (1, "a", 1.0)])  # 行序不同
        rep = diff_tables(a, b)
        self.assertTrue(rep.identical)
        self.assertEqual(rep.diff_ratio, 0.0)

    def test_add_remove_modify(self):
        a = T(self.schema, [(1, "a", 1.0), (2, "b", 2.0), (3, "c", 3.0)])
        b = T(self.schema, [(2, "b", 2.5), (3, "c", 3.0), (4, "d", 4.0)])
        rep = diff_tables(a, b)
        self.assertEqual((rep.added_count, rep.removed_count, rep.modified_count), (1, 1, 1))
        self.assertEqual(rep.field_diff_count, 1)
        fd = rep.field_diffs[0]
        self.assertEqual((fd.key, fd.column, fd.left, fd.right), ((2,), "price", 2.0, 2.5))
        self.assertEqual(rep.union_keys, 4)
        self.assertAlmostEqual(rep.diff_ratio, 3 / 4)

    def test_field_level_multi_column(self):
        a = T(self.schema, [(1, "a", 1.0)])
        b = T(self.schema, [(1, "x", 9.9)])
        rep = diff_tables(a, b)
        self.assertEqual(rep.modified_count, 1)
        self.assertEqual(rep.field_diff_count, 2)
        self.assertEqual({f.column for f in rep.field_diffs}, {"name", "price"})


class TestKeyAnomalies(unittest.TestCase):
    def setUp(self):
        self.schema = [Column("id", "int"), Column("v", "str")]

    def test_duplicate_keys_excluded_and_reported(self):
        a = T(self.schema, [(1, "x"), (1, "y"), (2, "z")])
        b = T(self.schema, [(1, "x"), (2, "z")])
        rep = diff_tables(a, b)
        self.assertEqual(rep.left_duplicate_keys, 2)  # 两行都视为重复，不参与对齐
        self.assertEqual(rep.common_keys, 1)
        self.assertFalse(rep.identical)
        self.assertIn((1,), rep.left_duplicate_samples)

    def test_missing_keys(self):
        a = T(self.schema, [(None, "x"), (1, "y")])
        b = T(self.schema, [(1, "y"), (None, "z"), (None, "w")])
        rep = diff_tables(a, b)
        self.assertEqual(rep.left_missing_keys, 1)
        self.assertEqual(rep.right_missing_keys, 2)
        self.assertEqual(rep.common_keys, 1)

    def test_missing_key_column_raises(self):
        a = T(self.schema, [(1, "x")])
        b = T([Column("k", "int"), Column("v", "str")], [(1, "x")])
        with self.assertRaises(ValueError):
            diff_tables(a, b)

    def test_composite_key(self):
        schema = [Column("a", "int"), Column("b", "str"), Column("v", "int")]
        a = T(schema, [(1, "x", 10), (1, "y", 20)])
        b = T(schema, [(1, "x", 10), (1, "y", 99)])
        rep = diff_tables(a, b, DiffConfig(key_columns=("a", "b")))
        self.assertEqual(rep.modified_count, 1)
        self.assertEqual(rep.field_diffs[0].key, (1, "y"))


class TestSchema(unittest.TestCase):
    def test_column_only_on_one_side(self):
        a = T([Column("id", "int"), Column("x", "int")], [(1, 5)])
        b = T([Column("id", "int"), Column("y", "str")], [(1, "s")])
        rep = diff_tables(a, b)
        kinds = {(i.kind, i.column) for i in rep.schema_issues}
        self.assertEqual(kinds, {("missing_in_right", "x"), ("missing_in_left", "y")})
        self.assertEqual(rep.compared_columns, [])
        self.assertFalse(rep.identical)

    def test_type_mismatch_not_compared(self):
        a = T([Column("id", "int"), Column("v", "int")], [(1, 5)])
        b = T([Column("id", "int"), Column("v", "str")], [(1, "5")])
        rep = diff_tables(a, b)
        self.assertEqual(rep.schema_issues[0].kind, "type_mismatch")
        self.assertEqual(rep.compared_columns, [])
        self.assertEqual(rep.field_diff_count, 0)

    def test_int_float_compatible(self):
        a = T([Column("id", "int"), Column("v", "int")], [(1, 5)])
        b = T([Column("id", "int"), Column("v", "float")], [(1, 5.0)])
        rep = diff_tables(a, b)
        self.assertEqual(rep.schema_issues, [])
        self.assertTrue(rep.identical)

    def test_column_order_irrelevant(self):
        a = T([Column("id", "int"), Column("v", "str")], [(1, "x")])
        b = T([Column("v", "str"), Column("id", "int")], [("x", 1)])
        self.assertTrue(diff_tables(a, b).identical)


class TestIgnoreAndTolerance(unittest.TestCase):
    def test_ignore_column(self):
        schema = [Column("id", "int"), Column("ts", "str"), Column("v", "int")]
        a = T(schema, [(1, "t1", 5)])
        b = T(schema, [(1, "t2", 5)])
        rep = diff_tables(a, b, DiffConfig(ignore_columns=("ts",)))
        self.assertTrue(rep.identical)
        self.assertEqual(rep.ignored_columns, ["ts"])
        self.assertIn("ts", rep.to_text())

    def test_tolerance_within_and_beyond(self):
        schema = [Column("id", "int"), Column("v", "float")]
        a = T(schema, [(1, 1.000), (2, 1.000)])
        b = T(schema, [(1, 1.004), (2, 1.500)])
        cfg = DiffConfig(tolerances={"v": (0.01, 0.0)})
        rep = diff_tables(a, b, cfg)
        self.assertEqual(rep.modified_count, 1)
        self.assertEqual(rep.field_diffs[0].key, (2,))
        self.assertEqual(rep.tolerance_rules, {"v": (0.01, 0.0)})

    def test_default_tolerance(self):
        schema = [Column("id", "int"), Column("v", "float")]
        a = T(schema, [(1, 100.0)])
        b = T(schema, [(1, 100.05)])
        rep = diff_tables(a, b, DiffConfig(default_rel_tol=1e-3))
        self.assertTrue(rep.identical)


class TestEdgeCases(unittest.TestCase):
    def test_both_empty(self):
        s = [Column("id", "int")]
        rep = diff_tables(T(s, []), T(s, []))
        self.assertTrue(rep.identical)
        self.assertEqual(rep.diff_ratio, 0.0)

    def test_one_side_empty(self):
        s = [Column("id", "int")]
        rep = diff_tables(T(s, [(1,), (2,)]), T(s, []))
        self.assertEqual(rep.removed_count, 2)
        self.assertEqual(rep.diff_ratio, 1.0)

    def test_report_text_and_dict(self):
        s = [Column("id", "int"), Column("v", "str")]
        rep = diff_tables(T(s, [(1, "a")]), T(s, [(1, "b"), (2, "c")]))
        text = rep.to_text()
        self.assertIn("新增", text)
        d = rep.to_dict()
        self.assertEqual(d["counts"]["added"], 1)
        self.assertEqual(d["counts"]["modified_rows"], 1)


if __name__ == "__main__":
    unittest.main()
