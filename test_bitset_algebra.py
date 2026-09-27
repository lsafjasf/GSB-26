"""对拍自测：BitSet 全部运算与朴素 set 实现对比。

覆盖：空集、上界内全集、超大稀疏集合、稠密集合、随机混合，
并强制三种表示组合（bitmap/bitmap、interval/interval、混合）分别对拍。
"""

import random
import sys
import unittest

sys.path.insert(0, ".")
from bitset_algebra import BitSet, _normalize


def make_cases():
    """返回 [(universe, set_a, set_b), ...] 覆盖各典型场景。"""
    rng = random.Random(42)
    cases = []
    # 空集
    cases.append((0, set(), set()))
    cases.append((1024, set(), set(range(1024))))
    # 上界内全集
    cases.append((4096, set(range(4096)), set(range(0, 4096, 3))))
    # 超大稀疏集合（bitmap 表示在此场景不可行：2^36 bits = 8GiB）
    big = 1 << 36
    sparse_a = {rng.randrange(big) for _ in range(2000)}
    sparse_b = {rng.randrange(big) for _ in range(2000)}
    sparse_b |= set(list(sparse_a)[:500])  # 构造部分重叠
    cases.append((big, sparse_a, sparse_b))
    # 稠密集合
    dense_a = {i for i in range(10000) if rng.random() < 0.9}
    dense_b = {i for i in range(10000) if rng.random() < 0.9}
    cases.append((10000, dense_a, dense_b))
    # 成段稀疏（区间表示主场）
    seg_a = set()
    seg_b = set()
    for _ in range(30):
        lo = rng.randrange(10**7)
        seg_a |= set(range(lo, lo + rng.randrange(1, 500)))
        lo = rng.randrange(10**7)
        seg_b |= set(range(lo, lo + rng.randrange(1, 500)))
    cases.append((10**7, seg_a, seg_b))
    # 随机中小集合，多密度
    for density in (0.001, 0.01, 0.1, 0.5, 0.99):
        u = 5000
        a = {i for i in range(u) if rng.random() < density}
        b = {i for i in range(u) if rng.random() < density}
        cases.append((u, a, b))
    # 不同 universe 混合
    cases.append((0, set(), set()))  # placeholder replaced below
    return cases


class TestNormalize(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(_normalize([]), [])
        self.assertEqual(_normalize([(5, 3)]), [])  # 空区间被丢弃
        self.assertEqual(_normalize([(2, 4), (0, 1)]), [(0, 4)])       # 相邻合并
        self.assertEqual(_normalize([(0, 10), (3, 5)]), [(0, 10)])     # 包含合并
        self.assertEqual(_normalize([(0, 1), (3, 4), (6, 6)]),
                         [(0, 1), (3, 4), (6, 6)])
        self.assertEqual(_normalize([(9, 9), (0, 0), (4, 5)]), [(0, 0), (4, 5), (9, 9)])


class TestDifferential(unittest.TestCase):
    def check_pair(self, u, sa, sb, ra, rb):
        """ra/rb 为强制表示后的 BitSet；sa/sb 为朴素集合真值。"""
        for res, expect in [
            (ra.union(rb), sa | sb),
            (ra.intersection(rb), sa & sb),
            (ra.difference(rb), sa - sb),
            (ra.symmetric_difference(rb), sa ^ sb),
        ]:
            res.assert_invariants()
            self.assertEqual(res.to_set(), expect)
            self.assertEqual(res.count(), len(expect))
            self.assertEqual(list(res), sorted(expect))  # 按序迭代
        self.assertEqual(ra.issubset(rb), sa <= sb)
        self.assertEqual(rb.issubset(ra), sb <= sa)
        self.assertEqual(ra.count(), len(sa))
        self.assertEqual(list(ra), sorted(sa))
        ra.assert_invariants()
        rb.assert_invariants()

    def test_all_regimes_all_repr_combos(self):
        for u, sa, sb in make_cases():
            auto_a = BitSet.from_ints(sa, universe=u)
            auto_b = BitSet.from_ints(sb, universe=u)
            auto_a.assert_invariants()
            auto_b.assert_invariants()
            with self.subTest(u=u, n=len(sa)):
                self.check_pair(u, sa, sb, auto_a, auto_b)  # 自动选择
                for ka in ("bitmap", "interval"):
                    for kb in ("bitmap", "interval"):
                        # 超大 universe 下强制 bitmap 会爆内存，跳过
                        if u > (1 << 24) and (ka == "bitmap" or kb == "bitmap"):
                            continue
                        self.check_pair(u, sa, sb,
                                        auto_a.as_representation(ka),
                                        auto_b.as_representation(kb))

    def test_random_fuzz(self):
        rng = random.Random(7)
        for trial in range(150):
            u = rng.choice([1, 2, 17, 100, 1024, 65536])
            d1, d2 = rng.random(), rng.random()
            sa = {i for i in range(u) if rng.random() < d1}
            sb = {i for i in range(u) if rng.random() < d2}
            ra = BitSet.from_ints(sa, universe=u)
            rb = BitSet.from_ints(sb, universe=u)
            self.check_pair(u, sa, sb, ra, rb)
            self.check_pair(u, sa, sb,
                            ra.as_representation("interval"),
                            rb.as_representation("bitmap"))

    def test_from_intervals_and_iteration(self):
        s = BitSet.from_intervals([(10, 20), (0, 5), (30, 30)], universe=100)
        s.assert_invariants()
        self.assertEqual(list(s), list(range(0, 6)) + list(range(10, 21)) + [30])
        self.assertIn(15, s)
        self.assertNotIn(6, s)
        self.assertNotIn(100, s)

    def test_auto_representation_choice(self):
        dense = (i for i in range(2000) if i % 3)  # 稠密且多段
        self.assertEqual(BitSet.from_ints(dense, universe=2000).representation,
                         "bitmap")  # 稠密 -> bitmap
        self.assertEqual(BitSet.from_ints([1, 10**9], universe=10**9 + 1).representation,
                         "interval")  # 稀疏 -> interval
        self.assertEqual(BitSet.full(1 << 30).representation, "interval")  # 单区间

    def test_with_universe(self):
        s = BitSet.from_ints(range(0, 1000, 2), universe=1000)
        grown = s.with_universe(10**6)
        self.assertEqual(grown.to_set(), s.to_set())
        self.assertEqual(grown.universe, 10**6)
        shrunk = s.with_universe(500)
        self.assertEqual(shrunk.to_set(), set(range(0, 500, 2)))
        shrunk.assert_invariants()
        empty = s.with_universe(0)
        self.assertEqual(empty.count(), 0)
        empty.assert_invariants()

    def test_equality_across_reprs(self):
        a = BitSet.from_ints([1, 2, 3, 100], universe=1000)
        self.assertEqual(a, a.as_representation("bitmap"))
        self.assertEqual(a, a.as_representation("interval"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
