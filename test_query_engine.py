"""并行 vs 串行对拍回归测试（仅标准库，unittest）。

覆盖：
- 小数据、大数据量下逐行一致
- 倾斜分片（大量空分片 / 数据全压一个分片 / 分片数 > 行数）
- 单分片走串行路径
- 聚合跨分片边界（最值落在边界、去重值在片间重复、计数/求和拆分）
- 空数据、全部被过滤、None 口径、重复执行确定性、并行度不变性
"""

import random
import unittest

from query_engine import (
    Aggregate,
    Filter,
    ParallelExecutor,
    Query,
    execute_parallel,
    execute_serial,
    shard_rows,
)


def make_rows(n: int, seed: int = 20261004) -> list[dict]:
    rnd = random.Random(seed)
    regions = ["cn", "us", "eu", "jp"]
    rows = []
    for i in range(n):
        rows.append({
            "id": i,
            "amount": rnd.randint(0, 10_000),
            "region": rnd.choice(regions),
            "maybe": None if rnd.random() < 0.05 else rnd.randint(0, 99),
        })
    return rows


SCAN_QUERY = Query(
    filter=Filter("amount", "gt", 9_900),
    projection=("id", "amount", "region"),
)
AGG_QUERY = Query(
    filter=Filter("id", "ge", 0),
    aggregates=(
        Aggregate("count"),
        Aggregate("count", "maybe"),
        Aggregate("sum", "amount"),
        Aggregate("min", "amount"),
        Aggregate("max", "amount"),
        Aggregate("distinct", "region"),
        Aggregate("distinct", "maybe"),
    ),
)
AGG_FILTERED = Query(
    filter=Filter("amount", "ge", 5_000),
    aggregates=(
        Aggregate("count"),
        Aggregate("sum", "amount"),
        Aggregate("min", "amount"),
        Aggregate("max", "amount"),
        Aggregate("distinct", "region"),
    ),
)


class DifferentialTests(unittest.TestCase):
    def assertParallelEqualsSerial(self, rows, query, workers=None,
                                   partitions=None):
        expected = execute_serial(rows, query)
        actual = execute_parallel(rows, query, workers=workers,
                                  partitions=partitions)
        self.assertEqual(actual, expected)
        return actual

    def test_small_data_every_parallelism(self):
        rows = make_rows(10)
        for w in (1, 2, 4, 8, 16):
            with self.subTest(workers=w, kind="scan"):
                self.assertParallelEqualsSerial(rows, SCAN_QUERY, workers=w)
            with self.subTest(workers=w, kind="agg"):
                self.assertParallelEqualsSerial(rows, AGG_QUERY, workers=w)

    def test_large_scan_row_by_row_identical(self):
        rows = make_rows(50_000)
        actual = self.assertParallelEqualsSerial(rows, SCAN_QUERY, workers=4)
        self.assertTrue(len(actual) > 0)  # 过滤确实有命中
        # 顺序必须严格为 id 升序（串行就是这个顺序）
        ids = [r["id"] for r in actual]
        self.assertEqual(ids, sorted(ids))

    def test_large_aggregate_cross_shard(self):
        rows = make_rows(50_000)
        for w in (2, 3, 4, 8):
            with self.subTest(workers=w):
                self.assertParallelEqualsSerial(rows, AGG_FILTERED, workers=w)

    def test_skewed_one_giant_shard(self):
        rows = make_rows(20_000)
        # 绝大多数数据压在一个分片，其余分片为空或只有 1 行
        partitions = [rows[:1], [], rows[1:19_999], [], rows[19_999:]]
        self.assertParallelEqualsSerial(rows, SCAN_QUERY,
                                        partitions=partitions)
        self.assertParallelEqualsSerial(rows, AGG_QUERY,
                                        partitions=partitions)

    def test_skewed_many_empty_tail_shards(self):
        rows = make_rows(1_000)
        partitions = shard_rows(rows, 1_000) + [[] for _ in range(500)]
        self.assertParallelEqualsSerial(rows, SCAN_QUERY,
                                        partitions=partitions)
        self.assertParallelEqualsSerial(rows, AGG_FILTERED,
                                        partitions=partitions)

    def test_single_partition_uses_serial_path(self):
        rows = make_rows(100)
        self.assertParallelEqualsSerial(rows, SCAN_QUERY, partitions=[rows])
        self.assertParallelEqualsSerial(rows, AGG_QUERY, partitions=[list(rows)])

    def test_more_shards_than_rows(self):
        rows = make_rows(3)
        for w in (4, 16):
            self.assertParallelEqualsSerial(rows, SCAN_QUERY, workers=w)
            self.assertParallelEqualsSerial(rows, AGG_QUERY, workers=w)

    def test_boundary_aggregations_handcrafted(self):
        # 构造 4 个分片，使关键值恰好落在分片边界两侧
        rows = [
            {"id": 0, "v": 10, "k": "a"},
            {"id": 1, "v": 10, "k": "b"},   # 分片0 末行：全局 min
            {"id": 2, "v": 20, "k": "a"},
            {"id": 3, "v": 20, "k": "c"},   # 分片1 末行
            {"id": 4, "v": 30, "k": "b"},   # 去重值 a/b 跨片重复
            {"id": 5, "v": 30, "k": "d"},
            {"id": 6, "v": 42, "k": "a"},   # 全局 max 在最后一片
        ]
        partitions = [rows[0:2], rows[2:4], rows[4:6], rows[6:7]]
        query = Query(aggregates=(
            Aggregate("count"),
            Aggregate("sum", "v"),
            Aggregate("min", "v"),
            Aggregate("max", "v"),
            Aggregate("distinct", "k"),
        ))
        [result] = execute_parallel(rows, query, partitions=partitions)
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["sum_v"], 162)
        self.assertEqual(result["min_v"], 10)
        self.assertEqual(result["max_v"], 42)
        self.assertEqual(result["distinct_k"], ["a", "b", "c", "d"])
        self.assertEqual(result["n_distinct_k"], 4)
        # 与串行对拍
        self.assertEqual(execute_serial(rows, query), [result])

    def test_boundary_min_max_split_even_odd_shards(self):
        # 同样的数据，换不同分片切法，结果必须一致（与切法无关）
        rows = [{"id": i, "v": (i * 7) % 13} for i in range(101)]
        query = Query(aggregates=(
            Aggregate("min", "v"), Aggregate("max", "v"),
            Aggregate("sum", "v"), Aggregate("count"),
        ))
        expected = execute_serial(rows, query)
        for cut in (2, 3, 5, 7, 50, 101):
            parts = shard_rows(rows, cut)
            self.assertEqual(execute_parallel(rows, query, partitions=parts),
                             expected, f"cut={cut}")

    def test_empty_data(self):
        empty: list[dict] = []
        self.assertEqual(execute_parallel(empty, SCAN_QUERY, workers=4),
                         execute_serial(empty, SCAN_QUERY))
        [agg] = execute_parallel(empty, Query(aggregates=(
            Aggregate("count"), Aggregate("sum", "x"),
            Aggregate("min", "x"), Aggregate("max", "x"),
            Aggregate("distinct", "x"),
        )), workers=4)
        self.assertEqual(agg["count"], 0)
        self.assertEqual(agg["sum_x"], 0)
        self.assertIsNone(agg["min_x"])
        self.assertIsNone(agg["max_x"])
        self.assertEqual(agg["distinct_x"], [])

    def test_all_rows_filtered_out(self):
        rows = make_rows(100)
        q = Query(filter=Filter("amount", "gt", 10 ** 9),
                  projection=("id",))
        self.assertEqual(execute_parallel(rows, q, workers=4), [])
        qa = Query(filter=Filter("amount", "gt", 10 ** 9),
                   aggregates=(Aggregate("count"), Aggregate("sum", "amount")))
        [r] = execute_parallel(rows, qa, workers=4)
        self.assertEqual(r, {"count": 0, "sum_amount": 0})

    def test_none_semantics(self):
        rows = [{"x": 1}, {"x": None}, {"x": 2}, {"x": None}]
        q = Query(aggregates=(
            Aggregate("count", "x"), Aggregate("sum", "x"),
            Aggregate("min", "x"), Aggregate("max", "x"),
            Aggregate("distinct", "x"),
        ))
        for cut in (1, 2, 3, 4):
            [r] = execute_parallel(rows, q, partitions=shard_rows(rows, cut))
            self.assertEqual(r["count_x"], 2)
            self.assertEqual(r["sum_x"], 3)
            self.assertEqual(r["min_x"], 1)
            self.assertEqual(r["max_x"], 2)
            self.assertEqual(r["distinct_x"], [1, 2])

    def test_repeated_runs_deterministic(self):
        rows = make_rows(2_000)
        runs = [execute_parallel(rows, AGG_FILTERED, workers=4)
                for _ in range(3)]
        self.assertTrue(all(r == runs[0] for r in runs))

    def test_parallelism_invariance(self):
        rows = make_rows(3_000)
        ref = execute_serial(rows, SCAN_QUERY)
        for w in (1, 2, 3, 4, 8):
            self.assertEqual(execute_parallel(rows, SCAN_QUERY, workers=w), ref)
        ref_agg = execute_serial(rows, AGG_QUERY)
        for w in (1, 2, 3, 4, 8):
            self.assertEqual(execute_parallel(rows, AGG_QUERY, workers=w),
                             ref_agg)

    def test_persistent_executor_matches_serial(self):
        rows = make_rows(20_000)
        with ParallelExecutor(workers=4) as ex:
            ex.bind(rows)
            for q in (SCAN_QUERY, AGG_QUERY, AGG_FILTERED):
                self.assertEqual(ex.execute(q), execute_serial(rows, q))
            # 换并行度重建池，结果仍一致
            ex.bind(rows)
            self.assertEqual(ex.execute(AGG_QUERY),
                             execute_serial(rows, AGG_QUERY))
        with ParallelExecutor(workers=8) as ex:
            ex.bind(rows)
            self.assertEqual(ex.execute(SCAN_QUERY),
                             execute_serial(rows, SCAN_QUERY))

    def test_persistent_executor_requires_bind(self):
        with ParallelExecutor(workers=2) as ex:
            with self.assertRaises(RuntimeError):
                ex.execute(SCAN_QUERY)

    def test_invalid_query_rejected(self):
        rows = make_rows(2)
        with self.assertRaises(ValueError):
            execute_parallel(rows, Query(filter=Filter("x", "??", 1)))
        with self.assertRaises(ValueError):
            execute_parallel(
                rows, Query(projection=("id",),
                            aggregates=(Aggregate("count"),)))
        with self.assertRaises(ValueError):
            execute_parallel(rows, Query(aggregates=(Aggregate("frob"),)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
