"""并行查询引擎边界用例集：小数据 / 倾斜分片 / 单分片 / 跨分片聚合。

运行: python3 test_qengine.py -v
"""

import os
import shutil
import tempfile
import unittest

from qengine import (Plan, Schema, assert_rows_equal, run_parallel, run_serial,
                     split_evenly)
from qengine import expr as E
from qengine.plan import (OrderKey, avg, collect_distinct, count,
                          count_distinct, count_star, max_, min_, sum_)
from qengine.table import read_csv_shard, write_csv_shard

SCHEMA = Schema([("user_id", "int"), ("region", "str"), ("amount", "int"),
                 ("score", "float"), ("status", "str"), ("ts", "int")])

WORKERS = (1, 2, 3, 5, 8)


def R(uid, region, amount, score, status, ts):
    return {"user_id": uid, "region": region, "amount": amount,
            "score": score, "status": status, "ts": ts}


def check_all(self, shards, plan, workers=WORKERS):
    """串行为基准，所有并行度必须逐行一致；返回串行结果。"""
    base = run_serial(shards, SCHEMA, plan)
    for w in workers:
        got = run_parallel(shards, SCHEMA, plan, w)
        try:
            assert_rows_equal(base, got)
        except AssertionError as e:
            self.fail(f"workers={w}: {e}")
    return base


class TestSmallData(unittest.TestCase):
    """小数据：几行也要保证语义正确。"""

    def setUp(self):
        self.rows = [
            R(1, "a", 5, 1.0, "ok", 1),
            R(2, None, 20, None, "ok", 2),
            R(1, "b", 30, 3.0, "err", 3),
            R(3, "a", 15, 2.0, None, 4),
        ]
        self.shards = split_evenly(self.rows, 3)

    def test_select_filter(self):
        plan = Plan(filter=E.gt(E.col("amount"), E.lit(10)),
                    select=(("user_id", E.col("user_id")),
                            ("amount", E.col("amount"))))
        rows = check_all(self, self.shards, plan)
        # 保持扫描序：ts=2,3,4
        self.assertEqual([r["user_id"] for r in rows], [2, 1, 3])
        self.assertEqual([r["amount"] for r in rows], [20, 30, 15])

    def test_null_filter_semantics(self):
        # amount 为 NULL 的行不满足 amount>10（三值逻辑：NULL 被过滤）
        plan = Plan(filter=E.gt(E.col("amount"), E.lit(10)),
                    select=(("ts", E.col("ts")),))
        rows = check_all(self, self.shards, plan)
        self.assertEqual(len(rows), 3)

    def test_group_by_with_null_key(self):
        plan = Plan(group_by=(E.col("region"),),
                    aggs=(count_star("cnt"), sum_(E.col("amount"), "s")))
        rows = check_all(self, self.shards, plan)
        got = {r["key_0"]: (r["cnt"], r["s"]) for r in rows}
        self.assertEqual(got, {"a": (2, 20), "b": (1, 30), None: (1, 20)})

    def test_empty_table(self):
        shards = [[]]
        plan = Plan(select=(("user_id", E.col("user_id")),))
        self.assertEqual(check_all(self, shards, plan), [])
        agg = Plan(aggs=(count_star("cnt"), avg(E.col("amount"), "a"),
                         min_(E.col("amount"), "lo"), max_(E.col("amount"), "hi")))
        rows = check_all(self, shards, agg)
        self.assertEqual(rows, [{"cnt": 0, "a": None, "lo": None, "hi": None}])

    def test_all_rows_filtered_out(self):
        plan = Plan(filter=E.eq(E.col("status"), E.lit("nope")),
                    select=(("user_id", E.col("user_id")),))
        self.assertEqual(check_all(self, self.shards, plan), [])


class TestSingleShard(unittest.TestCase):
    """单分片：并行度再高也只有一个任务，结果必须与串行一致。"""

    def test_single_shard(self):
        rows = [R(i % 3, "r" + str(i % 2), i, float(i), "ok", i)
                for i in range(50)]
        shards = [rows]
        plan = Plan(group_by=(E.col("region"),),
                    aggs=(count_star("cnt"), sum_(E.col("amount"), "s"),
                          min_(E.col("amount"), "lo"), max_(E.col("amount"), "hi"),
                          avg(E.col("amount"), "a"),
                          count_distinct(E.col("user_id"), "u")))
        check_all(self, shards, plan, workers=(1, 2, 4, 8))

    def test_more_workers_than_shards(self):
        shards = split_evenly([R(1, "x", i, 1.0, "ok", i) for i in range(5)], 2)
        plan = Plan(aggs=(count_star("cnt"), sum_(E.col("amount"), "s")))
        rows = check_all(self, shards, plan, workers=(1, 2, 8, 16))
        self.assertEqual(rows, [{"cnt": 5, "s": 10}])


class TestSkewedShards(unittest.TestCase):
    """倾斜分片：空分片 + 巨型分片 + 数据倾斜（热点组集中在个别分片）。"""

    def setUp(self):
        hot = [R(1, "hot", 1, 1.0, "ok", i) for i in range(200)]
        cold = [R(2, "cold", 100, 2.0, "err", 1000 + i) for i in range(5)]
        self.shards = [[], hot, [], cold, []]   # 大量空分片 + 数据集中

    def test_skewed_agg(self):
        plan = Plan(group_by=(E.col("region"),),
                    aggs=(count_star("cnt"), sum_(E.col("amount"), "s"),
                          min_(E.col("amount"), "lo"), max_(E.col("amount"), "hi"),
                          count_distinct(E.col("user_id"), "u")))
        rows = check_all(self, self.shards, plan)
        got = {r["key_0"]: r for r in rows}
        self.assertEqual(got["hot"]["cnt"], 200)
        self.assertEqual(got["hot"]["s"], 200)
        self.assertEqual(got["cold"]["cnt"], 5)
        self.assertEqual(got["cold"]["lo"], 100)
        self.assertEqual(got["cold"]["u"], 1)

    def test_skewed_select_order_preserved(self):
        plan = Plan(filter=E.eq(E.col("status"), E.lit("ok")),
                    select=(("ts", E.col("ts")),))
        rows = check_all(self, self.shards, plan)
        self.assertEqual([r["ts"] for r in rows], list(range(200)))


class TestCrossShardAggregation(unittest.TestCase):
    """聚合跨分片：每个口径的分片边界语义。"""

    def setUp(self):
        # 同一组 "g" 横跨全部分片；每组度量值分布在不同分片
        self.shards = [
            [R(1, "g", 10, 1.5, "ok", 1), R(2, "h", -5, 2.5, "ok", 2)],
            [R(1, "g", None, 3.5, "err", 3)],                      # 度量 NULL
            [R(3, "g", 7, None, "ok", 4), R(1, "g", 3, 0.5, "ok", 5)],
            [],                                                    # 空分片
            [R(2, "h", 8, 9.5, None, 6)],
        ]

    def test_count_sum_min_max(self):
        plan = Plan(group_by=(E.col("region"),),
                    aggs=(count_star("cnt"),
                          count(E.col("amount"), "cnt_amount"),
                          sum_(E.col("amount"), "s"),
                          min_(E.col("amount"), "lo"),
                          max_(E.col("amount"), "hi"),
                          avg(E.col("amount"), "a")))
        rows = check_all(self, self.shards, plan)
        g, h = rows[0], rows[1]
        self.assertEqual(g["key_0"], "g")
        self.assertEqual((g["cnt"], g["cnt_amount"]), (4, 3))   # NULL 不计入 count(列)
        self.assertEqual((g["s"], g["lo"], g["hi"]), (20, 3, 10))
        self.assertAlmostEqual(g["a"], 20 / 3)
        self.assertEqual((h["cnt"], h["s"], h["lo"], h["hi"]), (2, 3, -5, 8))

    def test_distinct_across_shards(self):
        plan = Plan(group_by=(E.col("region"),),
                    aggs=(count_distinct(E.col("user_id"), "u"),
                          collect_distinct(E.col("status"), "st")))
        rows = check_all(self, self.shards, plan)
        g, h = rows[0], rows[1]
        self.assertEqual(g["u"], 2)                    # 用户 1,3（跨 3 个分片）
        self.assertEqual(g["st"], ["err", "ok"])       # 排序后的去重集合，NULL 除外
        self.assertEqual(h["u"], 1)
        self.assertEqual(h["st"], ["ok"])              # h 组含一行 status=NULL，不计

    def test_global_scalar_agg(self):
        plan = Plan(aggs=(count_star("cnt"), sum_(E.col("amount"), "s"),
                          max_(E.col("score"), "top")))
        rows = check_all(self, self.shards, plan)
        self.assertEqual(rows, [{"cnt": 6, "s": 23, "top": 9.5}])

    def test_min_max_on_strings(self):
        plan = Plan(aggs=(min_(E.col("status"), "lo"), max_(E.col("status"), "hi")))
        rows = check_all(self, self.shards, plan)
        self.assertEqual(rows, [{"lo": "err", "hi": "ok"}])


class TestOrderAndLimit(unittest.TestCase):
    """跨分片排序：并列时按全局行号兜底，结果与并行度无关。"""

    def test_ties_broken_by_row_order(self):
        rows = [R(1, "a", 1, 5.0, "ok", i) for i in range(10)]
        shards = split_evenly(rows, 4)
        plan = Plan(select=(("ts", E.col("ts")), ("score", E.col("score"))),
                    order_by=(OrderKey(E.col("score"), desc=True),),
                    limit=5)
        out = check_all(self, shards, plan)
        # 全部同分 -> 必须按扫描序取前 5
        self.assertEqual([r["ts"] for r in out], [0, 1, 2, 3, 4])

    def test_nulls_sorting(self):
        rows = [R(1, "a", None, 1.0, "ok", 1), R(2, "a", 3, 1.0, "ok", 2),
                R(3, "a", 1, 1.0, "ok", 3)]
        shards = split_evenly(rows, 2)
        asc = Plan(select=(("amount", E.col("amount")),),
                   order_by=(OrderKey(E.col("amount"), desc=False),))
        desc = Plan(select=(("amount", E.col("amount")),),
                    order_by=(OrderKey(E.col("amount"), desc=True),))
        self.assertEqual([r["amount"] for r in check_all(self, shards, asc)],
                         [None, 1, 3])          # 升序 NULL 在前
        self.assertEqual([r["amount"] for r in check_all(self, shards, desc)],
                         [3, 1, None])          # 降序 NULL 在后

    def test_limit_zero(self):
        plan = Plan(select=(("ts", E.col("ts")),), limit=0)
        self.assertEqual(check_all(self, [[R(1, "a", 1, 1.0, "ok", 1)]], plan), [])

    def test_order_by_expression_no_topk_pushdown(self):
        # 排序键是表达式而非列引用：本地 top-k 下推回退为全量合流，
        # 结果仍必须与串行一致
        rows = [R(i % 7, "a", i, i / 3.0, "ok", i) for i in range(300)]
        shards = split_evenly(rows, 5)
        plan = Plan(
            select=(("amount", E.col("amount")), ("ts", E.col("ts"))),
            order_by=(OrderKey(E.mod(E.col("amount"), E.lit(13)), desc=True),
                      OrderKey(E.col("ts"), desc=False)),
            limit=11,
        )
        out = check_all(self, shards, plan)
        self.assertEqual(len(out), 11)
        expect = sorted(range(300), key=lambda i: (-(i % 13), i))[:11]
        self.assertEqual([r["ts"] for r in out], expect)


class TestFileShards(unittest.TestCase):
    """文件分片路径：与内存分片结果一致（worker 实际读文件的代码路径）。"""

    def test_file_roundtrip(self):
        tmp = tempfile.mkdtemp()
        try:
            rows = [R(i % 5, "r" + str(i % 3), i * 7 - 20, i / 10.0,
                      "ok" if i % 2 else None, i) for i in range(101)]
            shards = split_evenly(rows, 5)
            paths = []
            for i, s in enumerate(shards):
                p = os.path.join(tmp, f"shard-{i:03d}.csv")
                write_csv_shard(p, SCHEMA, s)
                paths.append(p)
            plan = Plan(group_by=(E.col("region"),),
                        aggs=(count_star("cnt"), sum_(E.col("amount"), "s"),
                              avg(E.col("score"), "a"),
                              count_distinct(E.col("user_id"), "u")))
            mem = run_serial(shards, SCHEMA, plan)
            for w in (1, 2, 4):
                assert_rows_equal(mem, run_parallel(paths, SCHEMA, plan, w))
            # 明细查询也走一遍文件路径
            sel = Plan(filter=E.ge(E.col("amount"), E.lit(0)),
                       select=(("ts", E.col("ts")), ("amount", E.col("amount"))))
            assert_rows_equal(run_serial(shards, SCHEMA, sel),
                              run_parallel(paths, SCHEMA, sel, 3))
        finally:
            shutil.rmtree(tmp)


class TestFloatSum(unittest.TestCase):
    """浮点求和：分片合流顺序不同可能产生末位差异，引擎用容差对拍。

    整型/字符串/计数/最值/去重均保证逐值一致；float 的 sum/avg 是
    唯一允许容差的口径（见 QE_README「聚合口径」）。
    """

    def test_float_sum_tolerance(self):
        rows = [R(1, "g", 1, 0.1, "ok", i) for i in range(1000)]
        shards = split_evenly(rows, 7)
        plan = Plan(aggs=(sum_(E.col("score"), "s"), avg(E.col("score"), "a")))
        base = run_serial(shards, SCHEMA, plan)
        for w in (2, 3, 4, 8):
            got = run_parallel(shards, SCHEMA, plan, w)
            assert_rows_equal(base, got)   # 容差内一致
            self.assertAlmostEqual(base[0]["s"], got[0]["s"], places=9)


if __name__ == "__main__":
    unittest.main()
