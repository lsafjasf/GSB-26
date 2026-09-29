"""对拍测试：索引执行（块级下推 + 提前终止）vs 全量扫描参考实现。

运行：python3 -m unittest discover -s tests -v
"""

import random
import unittest

from logengine import Store, execute, full_scan, parse, query


def build_edge_store() -> Store:
    """4 个块 × 5 条记录，ts = 1..20，字段分布经过设计以便断言扫描块数。"""
    store = Store()
    for block_id in range(4):
        for j in range(5):
            i = block_id * 5 + j
            rec = {
                "ts": i + 1,
                "service": "common" if j == 0 else f"svc{block_id}",
                "env": "prod" if block_id < 2 else "staging",
                "level": "error" if i % 7 == 0 else "info",
                "latency": i * 10,
                "user_id": 1000 + i,
                "ok": i % 2 == 0,
            }
            if i % 3 == 0:
                rec["trace_id"] = f"t{i}"
            store.append(rec)
        store.seal_block()
    return store


def assert_same(testcase, store, text):
    """对拍：两种执行路径的结果必须完全一致。"""
    q = parse(text)
    fast = execute(store, q)
    slow = full_scan(store, q)
    testcase.assertEqual(fast.data, slow.data, f"result mismatch for: {text}")
    m = fast.metrics
    testcase.assertEqual(m.total_blocks, m.scanned_blocks + m.pruned_blocks + m.skipped_limit_blocks)
    return fast


class TestDeterministic(unittest.TestCase):
    def setUp(self):
        self.store = build_edge_store()

    def test_single_block_time_range(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 3 AND ts <= 5")
        self.assertEqual([rec["ts"] for rec in r.data], [3, 4, 5])
        self.assertEqual(r.metrics.scanned_blocks, 1)
        self.assertEqual(r.metrics.pruned_blocks, 3)

    def test_cross_block_time_range(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 4 AND ts <= 12")
        self.assertEqual(len(r.data), 9)
        self.assertEqual(r.metrics.scanned_blocks, 3)
        self.assertEqual(r.metrics.pruned_blocks, 1)

    def test_time_range_covers_everything(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 0")
        self.assertEqual(len(r.data), 20)
        self.assertEqual(r.metrics.scanned_blocks, 4)
        self.assertEqual(r.metrics.pruned_blocks, 0)

    def test_boundary_inclusive(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 5 AND ts <= 6")
        self.assertEqual([rec["ts"] for rec in r.data], [5, 6])
        self.assertEqual(r.metrics.scanned_blocks, 2)

    def test_boundary_exclusive_empty(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts > 5 AND ts < 6")
        self.assertEqual(r.data, [])
        self.assertEqual(r.metrics.scanned_blocks, 0)

    def test_empty_time_range(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 1000")
        self.assertEqual(r.data, [])
        self.assertEqual(r.metrics.scanned_blocks, 0)

    def test_equality_prunes_blocks(self):
        r = assert_same(self, self.store, "SELECT * WHERE service = 'svc2'")
        self.assertEqual(len(r.data), 4)
        self.assertEqual(r.metrics.scanned_blocks, 1)

    def test_equality_no_match_anywhere(self):
        r = assert_same(self, self.store, "SELECT * WHERE service = 'nope'")
        self.assertEqual(r.data, [])
        self.assertEqual(r.metrics.scanned_blocks, 0)

    def test_equality_high_selectivity(self):
        r = assert_same(self, self.store, "SELECT * WHERE env = 'prod'")
        self.assertEqual(len(r.data), 10)
        self.assertEqual(r.metrics.scanned_blocks, 2)

    def test_not_equal(self):
        r = assert_same(self, self.store, "SELECT * WHERE env != 'prod'")
        self.assertEqual(len(r.data), 10)
        self.assertEqual(r.metrics.scanned_blocks, 2)
        self.assertEqual(r.metrics.pruned_blocks, 2)

    def test_numeric_range_pruning(self):
        r = assert_same(self, self.store, "SELECT * WHERE latency >= 110 AND latency <= 140")
        self.assertEqual([rec["latency"] for rec in r.data], [110, 120, 130, 140])
        self.assertEqual(r.metrics.scanned_blocks, 1)

    def test_low_selectivity_user_id(self):
        r = assert_same(self, self.store, "SELECT * WHERE user_id = 1007")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.metrics.scanned_blocks, 1)

    def test_missing_field_never_matches(self):
        r = assert_same(self, self.store, "SELECT * WHERE no_such_field = 'x'")
        self.assertEqual(r.data, [])
        self.assertEqual(r.metrics.scanned_blocks, 0)

    def test_missing_field_not_equal(self):
        # 字段缺失的记录对 != 也不匹配（与参考实现一致）
        r = assert_same(self, self.store, "SELECT * WHERE trace_id != 't0'")
        self.assertTrue(all("trace_id" in rec for rec in r.data))

    def test_partial_field_equality(self):
        r = assert_same(self, self.store, "SELECT * WHERE trace_id = 't6'")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]["ts"], 7)

    def test_bool_condition(self):
        r = assert_same(self, self.store, "SELECT * WHERE ok = true")
        self.assertEqual(len(r.data), 10)

    def test_cross_type_comparison_is_false(self):
        r = assert_same(self, self.store, "SELECT * WHERE service > 5")
        self.assertEqual(r.data, [])

    def test_limit_desc_early_termination(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 1 ORDER BY ts DESC LIMIT 3")
        self.assertEqual([rec["ts"] for rec in r.data], [20, 19, 18])
        self.assertEqual(r.metrics.scanned_blocks, 1)
        self.assertEqual(r.metrics.skipped_limit_blocks, 3)

    def test_limit_desc_with_filter(self):
        r = assert_same(self, self.store,
                        "SELECT * WHERE service = 'svc2' ORDER BY ts DESC LIMIT 2")
        self.assertEqual([rec["ts"] for rec in r.data], [15, 14])
        self.assertEqual(r.metrics.scanned_blocks, 1)
        self.assertEqual(r.metrics.pruned_blocks, 3)

    def test_limit_larger_than_matches(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 1 ORDER BY ts DESC LIMIT 100")
        self.assertEqual(len(r.data), 20)
        self.assertEqual(r.metrics.scanned_blocks, 4)

    def test_limit_zero(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 1 ORDER BY ts DESC LIMIT 0")
        self.assertEqual(r.data, [])

    def test_order_asc_no_early_termination(self):
        r = assert_same(self, self.store, "SELECT * WHERE ts >= 1 ORDER BY ts ASC LIMIT 3")
        self.assertEqual([rec["ts"] for rec in r.data], [1, 2, 3])
        self.assertEqual(r.metrics.scanned_blocks, 4)

    def test_tied_ts_limit_cuts_inside_tie_group(self):
        # 块内存在相同 ts 的并列组：块0=[1]，块1=[2,3,3,3,4]，块2=[5,5,5,6]
        # 倒序全序为 id 9,8,7,6,5,4,3,2,1,0；LIMIT 截在 ts=5 的并列组中间时，
        # 提前终止（块内从后往前）与全量扫描（稳定排序）必须给出同一条记录。
        tied = Store()
        specs = [[(1, 0)], [(2, 1), (3, 2), (3, 3), (3, 4), (4, 5)],
                 [(5, 6), (5, 7), (5, 8), (6, 9)]]
        for group in specs:
            for ts_val, rec_id in group:
                tied.append({"id": rec_id, "ts": ts_val})
            tied.seal_block()

        for limit, expected in [(1, [9]), (2, [9, 8]), (3, [9, 8, 7]),
                                (4, [9, 8, 7, 6]), (6, [9, 8, 7, 6, 5, 4])]:
            sql = f"SELECT * WHERE ts >= 1 ORDER BY ts DESC LIMIT {limit}"
            r = assert_same(self, tied, sql)
            self.assertEqual([rec["id"] for rec in r.data], expected)
            if limit < 4:
                # 最新块即可凑满，后续 2 个候选块均未触及
                self.assertEqual(r.metrics.scanned_blocks, 1)
                self.assertEqual(r.metrics.skipped_limit_blocks, 2)

        # 截在中间块（ts=3）的并列组中间
        r = assert_same(self, tied, "SELECT * WHERE ts >= 1 ORDER BY ts DESC LIMIT 7")
        self.assertEqual([rec["id"] for rec in r.data], [9, 8, 7, 6, 5, 4, 3])
        self.assertEqual(r.metrics.scanned_blocks, 2)
        self.assertEqual(r.metrics.skipped_limit_blocks, 1)

        # 并列决胜规则对称地适用于 ASC：先写入者优先
        r = assert_same(self, tied, "SELECT * WHERE ts >= 1 ORDER BY ts ASC LIMIT 4")
        self.assertEqual([rec["id"] for rec in r.data], [0, 1, 2, 3])

    def test_count(self):
        r = assert_same(self, self.store, "SELECT COUNT(*) WHERE level = 'error'")
        self.assertEqual(r.data, 3)

    def test_count_empty(self):
        r = assert_same(self, self.store, "SELECT COUNT(*) WHERE service = 'nope'")
        self.assertEqual(r.data, 0)

    def test_group_by(self):
        r = assert_same(self, self.store, "SELECT COUNT(*) WHERE ts >= 1 GROUP BY env")
        self.assertEqual(r.data, [("prod", 10), ("staging", 10)])

    def test_group_by_empty(self):
        r = assert_same(self, self.store, "SELECT COUNT(*) WHERE service = 'nope' GROUP BY env")
        self.assertEqual(r.data, [])

    def test_group_by_with_range(self):
        r = assert_same(self, self.store,
                        "SELECT COUNT(*) WHERE ts >= 6 AND ts <= 15 GROUP BY env")
        self.assertEqual(r.data, [("prod", 5), ("staging", 5)])
        self.assertEqual(r.metrics.scanned_blocks, 2)


def build_random_store(seed: int, blocks: int, per_block: int) -> Store:
    rng = random.Random(seed)
    store = Store()
    services = ["auth", "api", "web", "worker", "db", "cache"]
    levels = ["info", "warn", "error"]
    ts = 0
    for block_idx in range(blocks):
        for j in range(per_block):
            # 允许 0 增量，制造块内相同 ts 的并列组；每块首条强制 +1，
            # 以满足 Store 对块间时间区间严格递增的约束
            ts += 1 if block_idx > 0 and j == 0 else rng.randint(0, 3)
            rec = {
                "ts": ts,
                "service": rng.choice(services),
                "level": rng.choice(levels),
                "latency": rng.randint(0, 500),
                "user_id": rng.randint(0, 2000),
                "ok": rng.random() < 0.5,
            }
            if rng.random() < 0.7:
                rec["trace_id"] = f"t{rng.randint(0, 50)}"
            store.append(rec)
        store.seal_block()
    return store


def random_query(rng: random.Random, max_ts: int) -> str:
    fields = ["service", "level", "latency", "user_id", "ok", "trace_id", "ts"]
    conds = []
    for _ in range(rng.randint(1, 3)):
        f = rng.choice(fields)
        op = rng.choice(["=", "!=", "<", "<=", ">", ">="])
        if f in ("service", "level", "trace_id"):
            value = f"'{rng.choice(['auth', 'api', 'web', 'worker', 'db', 'cache', 'info', 'warn', 'error', 't0', 't7', 'nope'])}'"
            if op not in ("=", "!="):
                op = "="
        elif f == "ok":
            value = rng.choice(["true", "false"])
            op = "="
        elif f == "ts":
            value = str(rng.randint(0, max_ts))
        else:
            value = str(rng.randint(0, 500 if f == "latency" else 2000))
        conds.append(f"{f} {op} {value}")
    where = " AND ".join(conds)
    kind = rng.random()
    if kind < 0.45:
        q = f"SELECT * WHERE {where}"
    elif kind < 0.65:
        q = f"SELECT COUNT(*) WHERE {where}"
    elif kind < 0.8:
        q = f"SELECT COUNT(*) WHERE {where} GROUP BY {rng.choice(['service', 'level', 'ok'])}"
    else:
        direction = rng.choice(["ASC", "DESC"])
        q = f"SELECT * WHERE {where} ORDER BY ts {direction} LIMIT {rng.randint(0, 20)}"
    return q


class TestDifferentialFuzz(unittest.TestCase):
    def test_fuzz(self):
        store = build_random_store(seed=42, blocks=8, per_block=50)
        max_ts = store.blocks[-1].stats.max_ts
        rng = random.Random(2026)
        for i in range(400):
            text = random_query(rng, max_ts)
            q = parse(text)
            fast = execute(store, q)
            slow = full_scan(store, q)
            self.assertEqual(fast.data, slow.data, f"iteration {i}, query: {text}")
            m = fast.metrics
            self.assertEqual(
                m.total_blocks,
                m.scanned_blocks + m.pruned_blocks + m.skipped_limit_blocks,
                f"metrics invariant broken, query: {text}",
            )
            self.assertLessEqual(m.scanned_blocks, slow.metrics.scanned_blocks)
            self.assertLessEqual(m.scanned_records, slow.metrics.scanned_records)

    def test_fuzz_single_block_store(self):
        store = build_random_store(seed=7, blocks=1, per_block=200)
        max_ts = store.blocks[-1].stats.max_ts
        rng = random.Random(99)
        for i in range(100):
            text = random_query(rng, max_ts)
            q = parse(text)
            self.assertEqual(execute(store, q).data, full_scan(store, q).data,
                             f"iteration {i}, query: {text}")


class TestParser(unittest.TestCase):
    def test_case_insensitive_keywords(self):
        store = build_edge_store()
        r = query(store, "select * where TS >= 1 and ENV = 'prod'")
        # 关键字大小写不敏感，字段名大小写敏感：TS/ENV 不是字段 -> 空结果
        self.assertEqual(r.data, [])
        r2 = query(store, "SELECT * WHERE ts >= 1 AND env = 'prod'")
        self.assertEqual(len(r2.data), 10)

    def test_syntax_errors(self):
        from logengine import ParseError
        for bad in ["", "SELECT", "SELECT * WHERE", "SELECT * WHERE ts",
                    "SELECT * WHERE ts = ", "DELETE * WHERE ts = 1",
                    "SELECT * WHERE ts = 1 LIMIT -1",
                    "SELECT * WHERE ts = 1 ORDER BY ts"]:
            with self.assertRaises(ParseError, msg=bad):
                parse(bad)

    def test_string_escapes_and_double_quotes(self):
        store = build_edge_store()
        r = query(store, 'SELECT * WHERE service = "svc1"')
        self.assertEqual(len(r.data), 4)

    def test_order_by_non_ts_rejected(self):
        store = build_edge_store()
        with self.assertRaises(ValueError):
            query(store, "SELECT * WHERE ts >= 1 ORDER BY latency DESC")


if __name__ == "__main__":
    unittest.main()
