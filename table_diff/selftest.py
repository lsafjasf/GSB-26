"""tablediff 自测：单元测试 + 随机数据集对拍（与朴素全量比较核对）。

运行: python3 selftest.py
"""

import random
import sys
import unittest

from tablediff import Table, compare


# ---------------------------------------------------------------- 朴素实现
# 独立实现的朴素全量比较：不复用库内任何函数，作为对拍基准。
def naive_diff(left, right, key_cols, ignore, tolerances):
    lcols = [c for c, _ in left.columns]
    rcols = [c for c, _ in right.columns]
    common = [c for c in lcols if c in rcols]
    compared = [c for c in common if c not in set(ignore)]
    lidx = {c: i for i, c in enumerate(lcols)}
    ridx = {c: i for i, c in enumerate(rcols)}
    kidx_l = [lidx[c] for c in key_cols]
    kidx_r = [ridx[c] for c in key_cols]

    def build(rows, kidx):
        d, seen = {}, set()
        for row in rows:
            k = tuple(row[j] for j in kidx)
            if any(v is None for v in k) or k in seen:
                continue  # 缺失/重复主键：朴素实现同样排除
            seen.add(k)
            d[k] = row
        return d

    L = build(left.rows, kidx_l)
    R = build(right.rows, kidx_r)

    def eq(a, b, col):
        if a is None or b is None:
            return a is None and b is None
        t = tolerances.get(col, tolerances.get("*"))
        if t is not None and isinstance(a, (int, float)) and not isinstance(a, bool) \
                and isinstance(b, (int, float)) and not isinstance(b, bool):
            atol = t if isinstance(t, (int, float)) else t.get("atol", 0.0)
            rtol = 0.0 if isinstance(t, (int, float)) else t.get("rtol", 0.0)
            return abs(a - b) <= max(atol, rtol * max(abs(a), abs(b)))
        return a == b

    added = {k for k in R if k not in L}
    deleted = {k for k in L if k not in R}
    modified = {}
    for k in L.keys() & R.keys():
        diffs = {}
        for c in compared:
            lv, rv = L[k][lidx[c]], R[k][ridx[c]]
            if not eq(lv, rv, c):
                diffs[c] = (lv, rv)
        if diffs:
            modified[k] = diffs
    return added, deleted, modified


def report_sets(rep):
    added = {tuple(d["key"]) for d in rep.diff["added"]}
    deleted = {tuple(d["key"]) for d in rep.diff["deleted"]}
    modified = {tuple(m["key"]): {f["column"]: (f["left"], f["right"])
                                  for f in m["fields"]}
                for m in rep.diff["modified"]}
    return added, deleted, modified


# ---------------------------------------------------------------- 单元测试
def make_table(name, cols, rows):
    return Table(name=name, columns=cols, rows=[tuple(r) for r in rows])


class UnitTests(unittest.TestCase):
    COLS = [("id", "int"), ("name", "str"), ("price", "float")]

    def test_identical(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0), (2, "y", 2.0)])
        b = make_table("b", self.COLS, [(2, "y", 2.0), (1, "x", 1.0)])  # 行序不同
        rep = compare(a, b, key="id")
        self.assertTrue(rep.equal)
        self.assertEqual(rep.summary["identical_rows"], 2)

    def test_add_del_modify(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0), (2, "y", 2.0), (3, "z", 3.0)])
        b = make_table("b", self.COLS, [(1, "x", 1.0), (2, "Y", 2.0), (4, "w", 4.0)])
        rep = compare(a, b, key="id")
        s = rep.summary
        self.assertEqual((s["added"], s["deleted"], s["modified_rows"]), (1, 1, 1))
        self.assertEqual(rep.diff["modified"][0]["fields"],
                         [{"column": "name", "left": "y", "right": "Y"}])
        self.assertAlmostEqual(s["diff_row_ratio"], 1.0)

    def test_duplicate_key_reported(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0), (1, "dup", 9.9), (2, "y", 2.0)])
        b = make_table("b", self.COLS, [(1, "x", 1.0), (2, "y", 2.0)])
        rep = compare(a, b, key="id")
        self.assertEqual(rep.keys["duplicates_left_count"], 1)
        self.assertFalse(rep.summary["equal"])
        # 重复键首行仍参与比较，其余被排除且计数
        self.assertEqual(rep.keys["duplicates_left_rows_excluded"], 1)

    def test_missing_key_reported(self):
        a = make_table("a", self.COLS, [(None, "x", 1.0), (2, "y", 2.0)])
        b = make_table("b", self.COLS, [(2, "y", 2.0)])
        rep = compare(a, b, key="id")
        self.assertEqual(rep.keys["missing_left_count"], 1)
        self.assertEqual(rep.keys["missing_left_examples"][0]["row_index"], 0)

    def test_schema_mismatch(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0)])
        b = make_table("b", [("id", "int"), ("name", "int"), ("extra", "str")],
                       [(1, 7, "e")])
        rep = compare(a, b, key="id")
        self.assertEqual(rep.schema["right_only_columns"], ["extra"])
        self.assertEqual(rep.schema["type_mismatches"][0]["column"], "name")
        self.assertFalse(rep.schema["consistent"])

    def test_ignore_column_and_rule_recorded(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0)])
        b = make_table("b", self.COLS, [(1, "DIFFERENT", 1.0)])
        rep = compare(a, b, key="id", ignore_columns=["name"])
        self.assertTrue(rep.equal)
        self.assertEqual(rep.rules["ignored_columns"], ["name"])

    def test_tolerance(self):
        a = make_table("a", self.COLS, [(1, "x", 1.000), (2, "y", 100.0)])
        b = make_table("b", self.COLS, [(1, "x", 1.004), (2, "y", 100.5)])
        rep = compare(a, b, key="id", tolerances={"price": {"atol": 0.01},
                                                  "*": 0.0})
        # 1.000 vs 1.004 在 atol=0.01 内；100.0 vs 100.5 超出
        self.assertEqual(rep.summary["modified_rows"], 1)
        self.assertEqual(rep.rules["tolerances"]["price"]["atol"], 0.01)
        rep2 = compare(a, b, key="id", tolerances={"price": {"rtol": 0.01}})
        self.assertTrue(rep2.summary["modified_rows"] == 0)

    def test_empty_tables(self):
        a = make_table("a", self.COLS, [])
        b = make_table("b", self.COLS, [])
        rep = compare(a, b, key="id")
        self.assertTrue(rep.equal)
        self.assertEqual(rep.summary["rows_left"], 0)

    def test_one_side_empty(self):
        a = make_table("a", self.COLS, [])
        b = make_table("b", self.COLS, [(1, "x", 1.0)])
        rep = compare(a, b, key="id")
        self.assertEqual(rep.summary["added"], 1)
        self.assertEqual(rep.summary["added_ratio"], 1.0)

    def test_composite_key(self):
        cols = [("k1", "str"), ("k2", "int"), ("v", "float")]
        a = make_table("a", cols, [("a", 1, 1.0), ("a", 2, 2.0)])
        b = make_table("b", cols, [("a", 1, 1.0), ("a", 2, 3.0)])
        rep = compare(a, b, key=["k1", "k2"])
        self.assertEqual(rep.summary["modified_rows"], 1)
        self.assertEqual(tuple(rep.diff["modified"][0]["key"]), ("a", 2))

    def test_key_column_absent_raises(self):
        a = make_table("a", self.COLS, [(1, "x", 1.0)])
        b = make_table("b", [("name", "str")], [("x",)])
        with self.assertRaises(ValueError):
            compare(a, b, key="id")

    def test_from_dicts(self):
        t = Table.from_dicts("t", [{"id": 1, "v": 1.5}, {"id": 2, "v": None}])
        self.assertEqual(dict(t.columns)["v"], "float")


# ---------------------------------------------------------------- 随机对拍
def gen_value(rng, typ):
    if typ == "int":
        return rng.randint(-1000, 1000)
    if typ == "float":
        return round(rng.uniform(-100, 100), rng.choice([1, 2, 3, 6]))
    if typ == "str":
        return "".join(rng.choices("abcdefg", k=rng.randint(0, 6)))
    return rng.choice([True, False])


def gen_dataset(rng, seed_tag):
    ncols = rng.randint(2, 8)
    types = [rng.choice(["int", "float", "str", "bool"]) for _ in range(ncols)]
    cols = [f"c{i}" for i in range(ncols)]
    key_cols = ["id"]
    schema = [("id", "int")] + list(zip(cols, types))

    n = rng.randint(0, 120)
    base_rows = []
    for i in range(n):
        base_rows.append([i] + [gen_value(rng, t) for t in types])

    def mutate(rows, allow_struct):
        rows = [list(r) for r in rows]
        # 随机删行
        rows = [r for r in rows if rng.random() > 0.1]
        # 随机改值（含容差边界）
        for r in rows:
            for j, t in enumerate(types, start=1):
                p = rng.random()
                if p < 0.05:
                    r[j] = gen_value(rng, t)
                elif p < 0.08 and t in ("int", "float"):
                    r[j] = r[j] + rng.choice([0.001, 0.004, 0.02, -0.03])
        # 随机加行
        for _ in range(rng.randint(0, max(1, n // 8))):
            rows.append([rng.randint(0, max(n + 5, 6))]
                        + [gen_value(rng, t) for t in types])
        # 注入重复主键 / 缺失主键
        for r in rows:
            p = rng.random()
            if p < 0.03 and rows:
                r[0] = rng.choice(rows)[0]
            elif p < 0.05:
                r[0] = None
        rng.shuffle(rows)
        return rows

    left_rows = mutate(base_rows, True)
    right_rows = mutate(base_rows, True)

    # 结构扰动：右表偶尔多列/少列/换类型
    right_schema = list(schema)
    if rng.random() < 0.3:
        right_schema.append(("extra_r", "str"))
        for r in right_rows:
            r.append("r" + str(rng.randint(0, 9)))
    if rng.random() < 0.3 and len(right_schema) > 2:
        drop = rng.randrange(1, len(right_schema))
        right_schema.pop(drop)
        for r in right_rows:
            r.pop(drop)
    if rng.random() < 0.2:
        i = rng.randrange(1, len(right_schema))
        right_schema[i] = (right_schema[i][0],
                           rng.choice(["int", "float", "str", "bool"]))

    ignore = [c for c, _ in schema[1:] if rng.random() < 0.15]
    tol = {}
    if rng.random() < 0.5:
        tol["*"] = 0.005
    for c, t in schema[1:]:
        if t in ("int", "float") and rng.random() < 0.3:
            tol[c] = {"atol": 0.01, "rtol": rng.choice([0.0, 0.001])}

    left = Table(name=f"L{seed_tag}", columns=schema, rows=[tuple(r) for r in left_rows])
    right = Table(name=f"R{seed_tag}", columns=right_schema,
                  rows=[tuple(r) for r in right_rows])
    return left, right, key_cols, ignore, tol


class DifferentialTests(unittest.TestCase):
    def test_random_against_naive(self):
        for seed in range(300):
            rng = random.Random(seed)
            left, right, key_cols, ignore, tol = gen_dataset(rng, seed)
            with self.subTest(seed=seed):
                rep = compare(left, right, key=key_cols,
                              ignore_columns=ignore, tolerances=tol,
                              max_examples=10 ** 9)
                exp = naive_diff(left, right, key_cols, ignore, tol)
                got = report_sets(rep)
                self.assertEqual(got[0], exp[0], "added 不一致")
                self.assertEqual(got[1], exp[1], "deleted 不一致")
                self.assertEqual(got[2], exp[2], "modified 不一致")
                # 计数一致性
                s = rep.summary
                self.assertEqual(s["added"], len(exp[0]))
                self.assertEqual(s["deleted"], len(exp[1]))
                self.assertEqual(s["modified_rows"], len(exp[2]))
                self.assertEqual(s["modified_fields"],
                                 sum(len(v) for v in exp[2].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
