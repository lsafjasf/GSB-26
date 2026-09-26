"""单元测试：基本功能、合并误差界、边界情形。运行：python3 -m unittest -v"""

import random
import unittest
from collections import Counter

from space_saving import SpaceSaving


class TestBasic(unittest.TestCase):
    def test_update_and_query(self):
        ss = SpaceSaving(10)
        for x in "aabbbc":
            ss.update(x)
        self.assertEqual(ss.query("a"), 2)
        self.assertEqual(ss.query("b"), 3)
        self.assertEqual(ss.query("c"), 1)
        self.assertEqual(ss.query("zzz"), 0)
        self.assertEqual(ss.n, 6)

    def test_weighted_update(self):
        ss = SpaceSaving(5)
        ss.update("a", weight=10)
        ss.update("b", weight=3)
        self.assertEqual(ss.query("a"), 10)
        self.assertEqual(ss.n, 13)
        with self.assertRaises(ValueError):
            ss.update("c", weight=0)

    def test_capacity_never_exceeded(self):
        ss = SpaceSaving(50)
        for i in range(10_000):
            ss.update(i)
        self.assertLessEqual(len(ss), 50)

    def test_error_bound_value(self):
        ss = SpaceSaving(100)
        for i in range(1_000):
            ss.update(i)
        self.assertAlmostEqual(ss.error_bound(), 10.0)

    def test_topk_order_and_guarantee(self):
        rng = random.Random(1)
        stream = rng.choices(range(1000), weights=[1 / (i + 1) for i in range(1000)], k=100_000)
        exact = Counter(stream)
        ss = SpaceSaving(200)
        for x in stream:
            ss.update(x)
        top = ss.topk(10)
        self.assertEqual(len(top), 10)
        self.assertEqual(top[0][0], exact.most_common(1)[0][0])  # 重尾下冠军必中
        bound = ss.error_bound()
        for key, cnt in exact.items():
            if cnt > bound:
                self.assertIn(key, ss.counts)  # 高频键必被监控

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            SpaceSaving(0)
        with self.assertRaises(ValueError):
            SpaceSaving(2).topk(-1)


class TestMerge(unittest.TestCase):
    def _check_merge(self, shards, capacity):
        """分片统计 -> 合并 -> 与全流精确计数对拍，验证合并误差界 (sum N)/m。"""
        summaries = []
        for shard in shards:
            ss = SpaceSaving(capacity)
            for x in shard:
                ss.update(x)
            summaries.append(ss)
        merged = SpaceSaving.merge_all(summaries)
        exact = Counter(x for shard in shards for x in shard)
        n_total = sum(len(s) for s in shards)
        bound = merged.error_bound()
        self.assertAlmostEqual(bound, n_total / capacity)
        max_err = 0
        for key, est in merged.counts.items():
            max_err = max(max_err, abs(est - exact[key]))
        for key, cnt in exact.items():
            if key not in merged.counts:
                max_err = max(max_err, cnt)
        self.assertLessEqual(max_err, bound + 1e-9,
                             f"合并误差 {max_err} 超过界 {bound}")
        return merged, exact, bound, max_err

    def test_merge_zipf_shards(self):
        rng = random.Random(7)
        stream = rng.choices(range(5000), weights=[1 / (i + 1) for i in range(5000)], k=200_000)
        shards = [stream[i::4] for i in range(4)]  # 4 路分片
        merged, exact, bound, max_err = self._check_merge(shards, capacity=200)
        print(f"\n[merge zipf x4] N=200000 m=200 界={bound:.0f} 实际最大误差={max_err}")
        # 合并后冠军仍正确
        self.assertEqual(merged.topk(1)[0][0], exact.most_common(1)[0][0])

    def test_merge_many_distinct_shards(self):
        shards = [[f"k{i}_{j}" for j in range(20_000)] for i in range(3)]
        merged, exact, bound, max_err = self._check_merge(shards, capacity=500)
        print(f"[merge distinct x3] N=60000 m=500 界={bound:.0f} 实际最大误差={max_err}")

    def test_merge_unequal_capacity(self):
        a, b = SpaceSaving(100), SpaceSaving(500)
        for i in range(5_000):
            a.update(i % 50)
            b.update(i % 50)
        merged = a.merge(b)
        self.assertEqual(merged.capacity, 100)  # 取较小容量
        self.assertAlmostEqual(merged.error_bound(), 10_000 / 100)
        self.assertEqual(merged.query(0), 200)  # 高频键合并后精确

    def test_merge_is_associative_enough(self):
        """两两合并与多路合并的误差界一致。"""
        rng = random.Random(3)
        shards = [[rng.randrange(100) for _ in range(10_000)] for _ in range(3)]
        summaries = []
        for shard in shards:
            ss = SpaceSaving(50)
            for x in shard:
                ss.update(x)
            summaries.append(ss)
        pairwise = summaries[0].merge(summaries[1]).merge(summaries[2])
        multi = SpaceSaving.merge_all(summaries)
        self.assertEqual(pairwise.error_bound(), multi.error_bound())

    def test_merge_does_not_mutate(self):
        a, b = SpaceSaving(10), SpaceSaving(10)
        a.update("x")
        b.update("y")
        a.merge(b)
        self.assertEqual(dict(a.counts), {"x": 1})
        self.assertEqual(dict(b.counts), {"y": 1})


class TestEdgeCases(unittest.TestCase):
    def test_empty_stream(self):
        ss = SpaceSaving(100)
        self.assertEqual(ss.n, 0)
        self.assertEqual(ss.error_bound(), 0.0)
        self.assertEqual(ss.topk(10), [])
        self.assertEqual(ss.query("anything"), 0)
        merged = ss.merge(SpaceSaving(100))
        self.assertEqual(merged.n, 0)

    def test_single_key(self):
        ss = SpaceSaving(10)
        ss.update("only")
        self.assertEqual(ss.query("only"), 1)
        self.assertEqual(ss.topk(5), [("only", 1)])

    def test_all_keys_identical(self):
        ss = SpaceSaving(4)
        for _ in range(100_000):
            ss.update("same")
        self.assertEqual(ss.query("same"), 100_000)  # 精确无误
        self.assertEqual(len(ss), 1)

    def test_all_keys_distinct(self):
        ss = SpaceSaving(64)
        for i in range(100_000):
            ss.update(i)
        self.assertEqual(len(ss), 64)
        # 每个键真实计数为 1，误差 |est-1| <= N/m = 1562.5
        self.assertLessEqual(max(ss.counts.values()) - 1, ss.error_bound() + 1e-9)

    def test_merge_empty_with_nonempty(self):
        empty = SpaceSaving(50)
        full = SpaceSaving(50)
        for i in range(1_000):
            full.update(i % 10)
        merged = empty.merge(full)
        self.assertEqual(merged.n, 1_000)
        self.assertEqual(merged.query(3), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
