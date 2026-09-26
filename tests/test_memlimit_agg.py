"""Differential (dui-pai) tests: streaming+spill implementation vs. the
naive fully in-memory reference, plus edge-case and null-rule tests."""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memlimit_agg import (CrossTab, GroupByAggregator, reference_crosstab,
                          reference_groupby)

ALL_AGGS = {"count": None, "sum": "v", "min": "v", "max": "v",
            "count_distinct": "v"}


def make_records(specs):
    """specs: list of (key, value); builds record dicts."""
    return [{"g": k, "v": v} for k, v in specs]


class EdgeCaseTests(unittest.TestCase):
    def test_empty_input(self):
        agg = GroupByAggregator("g", ALL_AGGS, memory_budget=128)
        self.assertEqual(agg.result(), {})
        ct = CrossTab("g", "c", op="sum", value="v", memory_budget=128)
        self.assertEqual(ct.result().cells, {})

    def test_single_record(self):
        agg = GroupByAggregator("g", ALL_AGGS, memory_budget=1 << 20)
        agg.add({"g": "a", "v": 5})
        self.assertEqual(agg.result(),
                         {"a": {"count": 1, "sum": 5, "min": 5, "max": 5,
                                "count_distinct": 1}})

    def test_all_same_group(self):
        records = make_records([("only", i) for i in range(1000)])
        agg = GroupByAggregator("g", ALL_AGGS, memory_budget=256)
        agg.add_many(records)
        self.assertEqual(agg.result(),
                         {"only": {"count": 1000, "sum": sum(range(1000)),
                                   "min": 0, "max": 999,
                                   "count_distinct": 1000}})

    def test_float_group_keys(self):
        records = make_records([(1.5, 1), (2.5, 2), (1.5, 3), (0.1 + 0.2, 4)])
        agg = GroupByAggregator("g", ALL_AGGS, memory_budget=1 << 20)
        agg.add_many(records)
        res = agg.result()
        self.assertEqual(res[1.5]["count"], 2)
        self.assertEqual(res[1.5]["sum"], 4)
        self.assertEqual(res[2.5]["sum"], 2)
        self.assertEqual(res[0.1 + 0.2]["sum"], 4)  # float keys used as-is

    def test_int_float_key_equivalence(self):
        # 1 and 1.0 hash equal in Python -> same group (documented behavior)
        records = make_records([(1, 10), (1.0, 20)])
        agg = GroupByAggregator("g", {"count": None, "sum": "v"})
        agg.add_many(records)
        self.assertEqual(agg.result(), {1: {"count": 2, "sum": 30}})


class NullRuleTests(unittest.TestCase):
    def test_none_and_nan_keys_form_missing_group(self):
        records = make_records([(None, 1), (float("nan"), 2), ("x", 3),
                                (None, 4)])
        agg = GroupByAggregator("g", ALL_AGGS)
        agg.add_many(records)
        res = agg.result()
        self.assertIn(None, res)  # missing group present in output
        self.assertEqual(res[None], {"count": 3, "sum": 7, "min": 1,
                                     "max": 4, "count_distinct": 3})
        self.assertEqual(res["x"]["count"], 1)

    def test_missing_key_field(self):
        records = [{"v": 1}, {"g": "a", "v": 2}, {"v": 3}]
        agg = GroupByAggregator("g", {"count": None, "sum": "v"})
        agg.add_many(records)
        res = agg.result()
        self.assertEqual(res[None], {"count": 2, "sum": 4})
        self.assertEqual(res["a"], {"count": 1, "sum": 2})

    def test_none_and_nan_values_ignored_by_value_ops(self):
        records = make_records([("a", None), ("a", float("nan")), ("a", 5),
                                ("b", None)])
        agg = GroupByAggregator("g", ALL_AGGS)
        agg.add_many(records)
        res = agg.result()
        # count counts records; value ops ignore None/NaN
        self.assertEqual(res["a"], {"count": 3, "sum": 5, "min": 5, "max": 5,
                                    "count_distinct": 1})
        # all-missing value group: sum=0, min/max=None, distinct=0
        self.assertEqual(res["b"], {"count": 1, "sum": 0, "min": None,
                                    "max": None, "count_distinct": 0})

    def test_duplicate_records_counted_per_occurrence(self):
        records = make_records([("a", 7)] * 5)
        agg = GroupByAggregator("g", ALL_AGGS)
        agg.add_many(records)
        self.assertEqual(agg.result()["a"],
                         {"count": 5, "sum": 35, "min": 7, "max": 7,
                          "count_distinct": 1})


class DifferentialTests(unittest.TestCase):
    """Random data, tiny budgets to force many spills; compare with the
    fully in-memory reference implementation."""

    def random_records(self, n, n_groups, rng, null_rate=0.1):
        records = []
        for _ in range(n):
            r = rng.random()
            if r < null_rate / 2:
                key = None
            elif r < null_rate:
                key = float("nan")
            else:
                kind = rng.randrange(3)
                if kind == 0:
                    key = rng.randrange(n_groups)
                elif kind == 1:
                    key = "grp-%d" % rng.randrange(n_groups)
                else:
                    key = round(rng.random() * n_groups, 2)  # float keys
            v = rng.random()
            if v < null_rate / 2:
                value = None
            elif v < null_rate:
                value = float("nan")
            else:
                value = rng.randrange(-1000, 1000)
            records.append({"g": key, "v": value, "c": "col%d" % (abs(hash(key)) % 7 if key is not None else 0)})
        return records

    def check_groupby(self, records, budget):
        agg = GroupByAggregator("g", ALL_AGGS, memory_budget=budget)
        agg.add_many(records)
        got = agg.result()
        want = reference_groupby(records, "g", ALL_AGGS)
        self.assertEqual(got, want)
        return agg

    def test_differential_small_budget_many_spills(self):
        rng = random.Random(20260926)
        records = self.random_records(20_000, n_groups=500, rng=rng)
        agg = self.check_groupby(records, budget=4 << 10)  # 4 KiB
        self.assertGreater(agg.spill_count, 1)  # spills actually happened

    def test_differential_no_spill_matches_too(self):
        rng = random.Random(7)
        records = self.random_records(2_000, n_groups=50, rng=rng)
        agg = self.check_groupby(records, budget=64 << 20)
        self.assertEqual(agg.spill_count, 0)

    def test_differential_each_op(self):
        rng = random.Random(99)
        records = self.random_records(10_000, n_groups=200, rng=rng)
        for op, field in [("count", None), ("sum", "v"), ("min", "v"),
                          ("max", "v"), ("count_distinct", "v")]:
            agg = GroupByAggregator("g", {op: field}, memory_budget=2 << 10)
            agg.add_many(records)
            self.assertEqual(agg.result(),
                             reference_groupby(records, "g", {op: field}),
                             msg="op=%s" % op)

    def test_differential_high_cardinality(self):
        rng = random.Random(1234)
        records = self.random_records(300_000, n_groups=250_000, rng=rng,
                                      null_rate=0.02)
        self.check_groupby(records, budget=1 << 20)  # 1 MiB, forces spills

    def test_differential_skewed(self):
        rng = random.Random(555)
        records = self.random_records(100_000, n_groups=10, rng=rng)
        # 99% of records into one hot group
        for i, rec in enumerate(records):
            if rng.random() < 0.99:
                rec["g"] = "hot"
        self.check_groupby(records, budget=4 << 10)

    def test_differential_crosstab(self):
        rng = random.Random(31337)
        records = self.random_records(30_000, n_groups=100, rng=rng)
        for op, field in [("count", None), ("sum", "v"), ("min", "v"),
                          ("max", "v"), ("count_distinct", "v")]:
            ct = CrossTab("g", "c", op=op, value=field,
                          memory_budget=4 << 10)
            ct.add_many(records)
            got = ct.result()
            cells, row_totals, col_totals = reference_crosstab(
                records, "g", "c", op=op, value=field)
            self.assertEqual(got.cells, cells, msg="op=%s cells" % op)
            self.assertEqual(got.row_totals, row_totals)
            self.assertEqual(got.col_totals, col_totals)
            if op in ("sum", "count"):
                self.assertEqual(got.grand_total, sum(row_totals.values()))
        self.assertGreater(ct.spill_count, 1)

    def test_crosstab_missing_keys_visible(self):
        records = [{"g": None, "c": "x", "v": 1},
                   {"g": "a", "v": 2},            # missing col field
                   {"v": 3}]                       # both missing
        ct = CrossTab("g", "c", op="sum", value="v")
        ct.add_many(records)
        res = ct.result()
        self.assertEqual(res.cells[(None, "x")], 1)
        self.assertEqual(res.cells[("a", None)], 2)
        self.assertEqual(res.cells[(None, None)], 3)
        self.assertEqual(res.row_totals[None], 4)
        self.assertEqual(res.grand_total, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
