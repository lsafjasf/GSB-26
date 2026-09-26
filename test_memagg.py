"""Correctness tests for memagg, including differential (对拍) tests that
compare the spilling implementation against a naive fully in-memory reference.
Run: python3 test_memagg.py -v
"""

import math
import random
import sys
import unittest

sys.path.insert(0, ".")
from memagg import AGGS, GroupBy, Pivot, _is_missing, _norm_key

ALL_AGGS = list(AGGS)
TINY_BUDGET = 2 << 10  # 2 KiB: forces many spills even on small inputs


# --------------------------------------------------------------------- naive

def naive_groupby(records, aggs):
    """Independent fully in-memory reference implementation."""
    groups = {}
    for key, value in records:
        groups.setdefault(_norm_key(key), []).append(value)
    out = {}
    for key, vals in groups.items():
        nn = [v for v in vals if not _is_missing(v)]
        r = {}
        for agg in aggs:
            if agg == "count":
                r[agg] = len(vals)
            elif agg == "sum":
                r[agg] = sum(nn) if nn else None
            elif agg == "min":
                r[agg] = min(nn) if nn else None
            elif agg == "max":
                r[agg] = max(nn) if nn else None
            elif agg == "count_distinct":
                r[agg] = len(set(nn))
        out[key] = r
    return out


def naive_pivot(records, agg):
    cells = {}
    for row, col, value in records:
        cells.setdefault((_norm_key(row), _norm_key(col)), []).append(value)
    rows, cols, out = set(), set(), {}
    for (r, c), vals in cells.items():
        nn = [v for v in vals if not _is_missing(v)]
        rows.add(r)
        cols.add(c)
        if agg == "count":
            out[(r, c)] = len(vals)
        elif agg == "sum":
            out[(r, c)] = sum(nn) if nn else None
        elif agg == "min":
            out[(r, c)] = min(nn) if nn else None
        elif agg == "max":
            out[(r, c)] = max(nn) if nn else None
        elif agg == "count_distinct":
            out[(r, c)] = len(set(nn))
    from memagg import _sort_key
    return {"rows": sorted(rows, key=_sort_key),
            "cols": sorted(cols, key=_sort_key), "cells": out}


def run_groupby(records, aggs=ALL_AGGS, budget=TINY_BUDGET):
    gb = GroupBy(aggs, memory_budget=budget)
    gb.update(records)
    return gb.to_dict(), gb.spill_count


# ---------------------------------------------------------------------- tests

class EdgeCaseTests(unittest.TestCase):
    def test_empty_input(self):
        res, spills = run_groupby([])
        self.assertEqual(res, {})
        self.assertEqual(spills, 0)

    def test_single_record(self):
        res, _ = run_groupby([("a", 5)])
        self.assertEqual(res, {"a": {"sum": 5, "count": 1, "min": 5,
                                     "max": 5, "count_distinct": 1}})

    def test_single_record_null_value(self):
        res, _ = run_groupby([("a", None)])
        self.assertEqual(res, {"a": {"sum": None, "count": 1, "min": None,
                                     "max": None, "count_distinct": 0}})

    def test_all_same_group(self):
        records = [("only", i) for i in range(10_000)]
        res, spills = run_groupby(records)
        self.assertGreater(spills, 0, "tiny budget should force spills")
        self.assertEqual(res["only"], {"sum": sum(range(10_000)),
                                       "count": 10_000, "min": 0,
                                       "max": 9999, "count_distinct": 10_000})

    def test_float_group_keys(self):
        records = [(0.1 * i, i) for i in range(100)]
        records += [(float("nan"), 1), (None, 2)]  # both -> missing-key group
        res, _ = run_groupby(records)
        self.assertEqual(len(res), 101)
        self.assertEqual(res[None], {"sum": 3, "count": 2, "min": 1,
                                     "max": 2, "count_distinct": 2})
        self.assertEqual(res[0.1 * 3], {"sum": 3, "count": 1, "min": 3,
                                        "max": 3, "count_distinct": 1})

    def test_missing_key_normalization(self):
        records = [(None, 1), (float("nan"), 2), (None, None)]
        res, _ = run_groupby(records)
        self.assertEqual(list(res), [None])
        self.assertEqual(res[None]["count"], 3)
        self.assertEqual(res[None]["sum"], 3)

    def test_null_values_skipped_but_counted(self):
        records = [("g", None), ("g", float("nan")), ("g", 4), ("g", 2)]
        res, _ = run_groupby(records)
        self.assertEqual(res["g"], {"sum": 6, "count": 4, "min": 2,
                                    "max": 4, "count_distinct": 2})

    def test_duplicate_records_kept(self):
        records = [("k", 7)] * 5
        res, _ = run_groupby(records)
        self.assertEqual(res["k"]["count"], 5)
        self.assertEqual(res["k"]["sum"], 35)
        self.assertEqual(res["k"]["count_distinct"], 1)

    def test_bool_keys_normalized_to_int(self):
        records = [(True, 1), (1, 2), (False, 3), (0, 4)]
        res, _ = run_groupby(records)
        self.assertEqual(res[1]["count"], 2)
        self.assertEqual(res[0]["count"], 2)

    def test_results_single_pass(self):
        gb = GroupBy(["count"], memory_budget=TINY_BUDGET)
        gb.add("x", 1)
        list(gb.results())
        with self.assertRaises(RuntimeError):
            list(gb.results())


class DifferentialTests(unittest.TestCase):
    """对拍: spilling implementation vs naive in-memory reference."""

    def make_random_records(self, seed, n, key_space, allow_nulls=True):
        rng = random.Random(seed)
        keys = ([None, float("nan")] if allow_nulls else []) + [
            rng.choice([
                rng.randint(-key_space, key_space),
                rng.randrange(-key_space, key_space) * 0.5,  # exact floats
                f"k{rng.randrange(key_space)}",
            ]) for _ in range(key_space)
        ]
        records = []
        for _ in range(n):
            k = rng.choice(keys)
            r = rng.random()
            if allow_nulls and r < 0.1:
                v = None if r < 0.05 else float("nan")
            elif r < 0.6:
                v = rng.randint(-1000, 1000)
            else:
                v = rng.randint(-1000, 1000) * 0.5  # exactly representable
            records.append((k, v))
        return records

    def check_differential(self, records, budget):
        expected = naive_groupby(records, ALL_AGGS)
        got, spills = run_groupby(records, ALL_AGGS, budget)
        self.assertEqual(got, expected)
        return spills

    def test_random_small_budget_many_seeds(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                records = self.make_random_records(seed, n=3000, key_space=300)
                spills = self.check_differential(records, TINY_BUDGET)
                self.assertGreater(spills, 0)

    def test_no_spill_matches_too(self):
        records = self.make_random_records(99, n=1000, key_space=100)
        self.check_differential(records, 1 << 30)

    def test_skewed_distribution(self):
        rng = random.Random(7)
        records = [("hot" if rng.random() < 0.99 else f"cold{i % 50}",
                    rng.randint(0, 100)) for i in range(50_000)]
        self.check_differential(records, TINY_BUDGET)

    def test_high_cardinality(self):
        rng = random.Random(11)
        records = [(rng.randrange(300_000), rng.randint(0, 1000))
                   for _ in range(200_000)]
        spills = self.check_differential(records, 1 << 20)  # 1 MiB
        self.assertGreater(spills, 0)

    def test_all_null_values(self):
        records = [(i % 10, None) for i in range(1000)]
        self.check_differential(records, TINY_BUDGET)

    def test_float_sum_close_on_general_floats(self):
        # Arbitrary floats: partial-sum order differs, so compare with
        # relative tolerance (integers/exact floats above compare exactly).
        rng = random.Random(3)
        records = [(rng.randrange(50), rng.uniform(-100, 100))
                   for _ in range(5000)]
        expected = naive_groupby(records, ["sum"])
        got, _ = run_groupby(records, ["sum"], TINY_BUDGET)
        for k, v in expected.items():
            self.assertTrue(math.isclose(got[k]["sum"], v["sum"],
                                         rel_tol=1e-9, abs_tol=1e-9))


class PivotTests(unittest.TestCase):
    def test_empty(self):
        pv = Pivot("sum", memory_budget=TINY_BUDGET)
        self.assertEqual(pv.result(), {"rows": [], "cols": [], "cells": {}})

    def test_single_cell(self):
        pv = Pivot("sum", memory_budget=TINY_BUDGET)
        pv.add("r1", "c1", 10)
        self.assertEqual(pv.result(), {"rows": ["r1"], "cols": ["c1"],
                                       "cells": {("r1", "c1"): 10}})

    def test_missing_row_and_col_keys(self):
        pv = Pivot("count", memory_budget=TINY_BUDGET)
        pv.add(None, "c1", 5)
        pv.add("r1", float("nan"), 5)
        pv.add(None, None, 5)
        res = pv.result()
        self.assertEqual(res["cells"][(None, "c1")], 1)
        self.assertEqual(res["cells"][("r1", None)], 1)
        self.assertEqual(res["cells"][(None, None)], 1)
        self.assertIn(None, res["rows"])
        self.assertIn(None, res["cols"])

    def test_pivot_differential_all_aggs(self):
        rng = random.Random(42)
        for agg in AGGS:
            with self.subTest(agg=agg):
                records = []
                for _ in range(5000):
                    r = rng.choice([None, f"row{rng.randrange(40)}"])
                    c = rng.choice([None, rng.randrange(15) * 0.5])
                    v = rng.choice([None, rng.randint(0, 500)])
                    records.append((r, c, v))
                pv = Pivot(agg, memory_budget=TINY_BUDGET)
                pv.update(records)
                self.assertGreater(pv.spill_count, 0)
                self.assertEqual(pv.result(), naive_pivot(records, agg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
