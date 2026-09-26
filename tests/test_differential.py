"""对拍测试：剪枝查询结果必须与全量扫描完全一致。"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logq import LogStore, parse, ParseError


def make_record(rng, ts):
    rec = {
        "ts": ts,
        "level": rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR"]),
        "status": rng.choices([200, 301, 404, 500, 599], weights=[70, 10, 10, 8, 2])[0],
        "latency": round(rng.uniform(0, 2000), 2),
        "service": rng.choice(["api", "web", "worker"]),
    }
    if rng.random() < 0.1:
        del rec["service"]  # 制造字段缺失
    if rng.random() < 0.05:
        rec["trace_id"] = f"t-{rng.randint(0, 10**6)}"
    return rec


def build_store(rng, n_records, chunk_size):
    store = LogStore(chunk_size=chunk_size)
    base = 1_700_000_000
    ts = base
    recs = []
    for _ in range(n_records):
        ts += rng.randint(0, 5)  # ts 单调不减，允许相等（并列）
        recs.append(make_record(rng, ts))
    store.ingest(recs)
    store.finalize()
    return store, base, ts


def random_query(rng, base, ts_max):
    conds = []
    # 时间范围：可能为空区间、单点、覆盖全部
    style = rng.random()
    if style < 0.7:
        lo = rng.randint(base - 10, ts_max + 10)
        hi = rng.randint(base - 10, ts_max + 10)
        if lo > hi:
            lo, hi = hi, lo
        conds.append(f"ts>={lo}")
        conds.append(f"ts<={hi}")
    elif style < 0.8:
        conds.append(f"ts={rng.randint(base, ts_max)}")
    if rng.random() < 0.5:
        conds.append(f'level="{rng.choice(["INFO", "WARN", "ERROR", "DEBUG"])}"')
    if rng.random() < 0.4:
        conds.append(f"status={rng.choice([200, 301, 404, 500, 599, 418])}")
    if rng.random() < 0.4:
        op = rng.choice([">=", "<=", ">", "<"])
        conds.append(f"latency{op}{round(rng.uniform(0, 2000), 1)}")
    if rng.random() < 0.3:
        conds.append(f'service="{rng.choice(["api", "web", "worker", "ghost"])}"')
    if rng.random() < 0.15:
        conds.append(f'level!="{rng.choice(["INFO", "WARN"])}"')
    q = " AND ".join(conds)
    stage = rng.random()
    if stage < 0.35:
        q += f" | count by {rng.choice(['level', 'status', 'service'])}"
    elif stage < 0.65:
        q += f" | top {rng.randint(1, 50)}"
    return q


class TestDifferential(unittest.TestCase):
    """随机化对拍：多种块大小、多种数据规模。"""

    def run_differential(self, seed, n_records, chunk_size, n_queries):
        rng = random.Random(seed)
        store, base, ts_max = build_store(rng, n_records, chunk_size)
        for i in range(n_queries):
            q = random_query(rng, base, ts_max)
            fast = store.query(q)
            slow = store.full_scan(q)
            self.assertEqual(fast.scanned_chunks + fast.skipped_chunks,
                             len(store.chunks), f"块计数不一致: {q}")
            if fast.counts is not None:
                self.assertEqual(fast.counts, slow.counts, f"count by 结果不一致: {q}")
            else:
                self.assertEqual(fast.rows, slow.rows, f"rows 结果不一致: {q}")

    def test_single_chunk(self):
        self.run_differential(seed=1, n_records=500, chunk_size=1000, n_queries=100)

    def test_cross_chunk(self):
        self.run_differential(seed=2, n_records=5000, chunk_size=300, n_queries=150)

    def test_many_small_chunks(self):
        self.run_differential(seed=3, n_records=2000, chunk_size=50, n_queries=150)

    def test_tiny_and_empty(self):
        # 极小数据集与空数据集
        for n in (0, 1, 2, 7):
            self.run_differential(seed=10 + n, n_records=n, chunk_size=3, n_queries=50)

    def test_high_hit_rate(self):
        # 字段命中率极高：status=200 占 70%
        rng = random.Random(20)
        store, base, ts_max = build_store(rng, 3000, 200)
        for q in ("status=200", 'level="INFO"', "latency>=0", f"ts>={base}"):
            self.assertEqual(store.query(q).rows, store.full_scan(q).rows)

    def test_low_hit_rate_and_empty(self):
        rng = random.Random(21)
        store, base, ts_max = build_store(rng, 3000, 200)
        for q in ("status=599", "status=418", 'service="ghost"',
                  "latency>99999", f"ts>={ts_max + 1000}"):
            fast, slow = store.query(q), store.full_scan(q)
            self.assertEqual(fast.rows, slow.rows)
            if q in ("status=418", 'service="ghost"', "latency>99999"):
                self.assertEqual(fast.rows, [])
                self.assertEqual(fast.scanned_chunks, 0, f"应全部跳过: {q}")

    def test_boundary_timestamps(self):
        # 边界时间：范围端点恰好等于块的最小/最大 ts
        store = LogStore(chunk_size=10)
        recs = [{"ts": t, "v": t} for t in range(100)]
        store.ingest(recs)
        store.finalize()
        qs = [
            "ts>=0 AND ts<=9", "ts>=9 AND ts<=10", "ts>=10 AND ts<=19",
            "ts=0", "ts=99", "ts>=99", "ts<=0", "ts>99", "ts<0",
            "ts>=0 AND ts<=99", "ts>=50", "ts<50",
        ]
        for q in qs:
            self.assertEqual(store.query(q).rows, store.full_scan(q).rows, q)
        # 恰好整块边界：块 [0..9] 应被 ts>=10 跳过
        r = store.query("ts>=10")
        self.assertEqual(r.rows, store.full_scan("ts>=10").rows)
        self.assertGreater(r.skipped_chunks, 0)

    def test_top_n_early_termination(self):
        store = LogStore(chunk_size=100)
        recs = [{"ts": t, "v": t} for t in range(10_000)]
        store.ingest(recs)
        store.finalize()
        for n in (1, 5, 100, 1000):
            q = f"v>=0 | top {n}"
            fast, slow = store.query(q), store.full_scan(q)
            self.assertEqual(fast.rows, slow.rows)
            self.assertEqual(len(fast.rows), n)
            self.assertEqual(fast.rows[0]["ts"], 9999)
            self.assertLess(fast.scanned_chunks, len(store.chunks),
                            f"top {n} 不应扫描全部块")
        # top N 大于数据总量
        fast = store.query("v>=0 | top 99999")
        self.assertEqual(len(fast.rows), 10_000)

    def test_top_n_with_ties(self):
        # 大量相同 ts，验证并列时结果确定且与全量扫描一致
        rng = random.Random(30)
        store = LogStore(chunk_size=20)
        recs = [{"ts": rng.randint(0, 10), "v": i} for i in range(500)]
        store.ingest(recs)
        store.finalize()
        for n in (1, 3, 17, 100):
            q = f"ts>=0 | top {n}"
            self.assertEqual(store.query(q).rows, store.full_scan(q).rows)

    def test_count_by(self):
        rng = random.Random(40)
        store, base, ts_max = build_store(rng, 4000, 250)
        for q in ("| count by level",
                  f"ts>={base} AND ts<={ts_max} | count by status",
                  'level="ERROR" | count by service',
                  "status=418 | count by level"):
            fast, slow = store.query(q), store.full_scan(q)
            self.assertEqual(fast.counts, slow.counts, q)
        # 空结果的分组计数应为空字典
        self.assertEqual(store.query("status=418 | count by level").counts, {})

    def test_parser_errors(self):
        for bad in ("ts>>=1", "ts=", "| top 0", "a=1 | count by", "a=1 | foo",
                    "a=1 | top 3 | top 4", "a=1 | count by x | top 2", "ts=1 AND"):
            with self.assertRaises(ParseError, msg=bad):
                parse(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
