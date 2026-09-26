"""对拍测试：BitSet 两种表示 vs 朴素 set，外加不变量断言。

运行: python3 test_bitset.py [-v]
"""
import random
import unittest

from bitsetlib import (
    BitSet, bm_from_values, bm_run_count, bm_to_intervals,
    iv_union, iv_intersection, iv_difference, iv_symmetric_difference,
    iv_issubset, normalize_intervals, assert_normalized, prefer_interval,
)


def check_all(tc, A, B, ref_a, ref_b):
    """对一对集合做全部运算对拍 + 不变量断言。"""
    for S in (A, B, A | B, A & B, A - B, B - A, A ^ B):
        S.assert_invariants()
    tc.assertEqual(set(A | B), ref_a | ref_b)
    tc.assertEqual(set(A & B), ref_a & ref_b)
    tc.assertEqual(set(A - B), ref_a - ref_b)
    tc.assertEqual(set(B - A), ref_b - ref_a)
    tc.assertEqual(set(A ^ B), ref_a ^ ref_b)
    tc.assertEqual(A <= B, ref_a <= ref_b)
    tc.assertEqual(B <= A, ref_b <= ref_a)
    tc.assertEqual(len(A), len(ref_a))
    tc.assertEqual(len(B), len(ref_b))
    tc.assertEqual(list(A), sorted(ref_a))          # 按序迭代
    tc.assertEqual(list(B), sorted(ref_b))
    tc.assertEqual(A == B, ref_a == ref_b)
    probes = list(ref_a)[:3] + list(ref_b)[:3] + [0, A.universe - 1]
    for v in probes:
        if 0 <= v < A.universe:
            tc.assertEqual(v in A, v in ref_a)
            tc.assertEqual(v in B, v in ref_b)


def random_subset(rng, upper, density):
    n = int(upper * density)
    return set(rng.sample(range(upper), min(n, upper)))


class EdgeCases(unittest.TestCase):
    def test_empty(self):
        for U in (0, 1, 100):
            A, B = BitSet.empty(U), BitSet.empty(U)
            check_all(self, A, B, set(), set())
            self.assertEqual(A.count(), 0)
            self.assertEqual(list(A), [])

    def test_full_universe(self):
        U = 5000
        A = BitSet.full(U)
        B = BitSet.from_ints(range(0, U, 2), U)
        check_all(self, A, B, set(range(U)), set(range(0, U, 2)))
        self.assertEqual(A.count(), U)

    def test_interval_normalization(self):
        # 乱序、重叠、相邻、空区间混合输入
        S = BitSet.from_intervals(
            [(5, 8), (1, 3), (3, 5), (9, 9), (20, 15), (12, 14), (13, 16)], 10000)
        self.assertEqual(S.kind, BitSet.INTERVAL)
        self.assertEqual(list(S.data), [(1, 9), (12, 16)])
        assert_normalized(S.data)
        self.assertEqual(set(S), set(range(1, 10)) | set(range(12, 17)))

    def test_bounds_validation(self):
        with self.assertRaises(ValueError):
            BitSet.from_ints([0, 100], 100)
        with self.assertRaises(ValueError):
            BitSet.from_ints([-1], 100)
        with self.assertRaises(ValueError):
            BitSet.from_intervals([(90, 100)], 100)
        with self.assertRaises(ValueError):
            BitSet.from_ints([1], 10).union(BitSet.from_ints([1], 11))

    def test_set_universe(self):
        A = BitSet.from_ints([1, 50, 99], 100)
        grown = A.set_universe(10 ** 6)          # 扩界：免费，内容不变
        self.assertEqual(set(grown), {1, 50, 99})
        self.assertEqual(grown.universe, 10 ** 6)
        grown.assert_invariants()
        shrunk = A.set_universe(60)              # 缩界：裁掉 99
        self.assertEqual(set(shrunk), {1, 50})
        shrunk.assert_invariants()
        empty = A.set_universe(0)
        self.assertEqual(set(empty), set())
        empty.assert_invariants()
        # 稠密集合缩界后仍一致
        F = BitSet.full(1000).set_universe(300)
        self.assertEqual(set(F), set(range(300)))
        F.assert_invariants()

    def test_auto_representation_choice(self):
        # 超大上界 + 极稀疏 -> 区间
        S = BitSet.from_ints([1, 10 ** 9, 10 ** 12], 1 << 40)
        self.assertEqual(S.kind, BitSet.INTERVAL)
        # 小上界 + 稠密 -> 位图
        D = BitSet.from_ints(range(1000), 1000)
        self.assertEqual(D.kind, BitSet.BITMAP)
        # 全集：区间数 k=1，上界足够大时区间表示赢
        F = BitSet.full(100000)
        self.assertEqual(F.kind, BitSet.INTERVAL)
        self.assertEqual(list(F.data), [(0, 99999)])
        # 运算结果也会重新决策：稠密位图交集成稀疏结果 -> 区间
        A = BitSet.from_ints(range(0, 10000, 2), 10000)
        B = BitSet.from_ints([3, 7778], 10000)
        R = A & B
        self.assertEqual(set(R), {7778})
        self.assertEqual(R.kind, BitSet.INTERVAL)


class RawPrimitives(unittest.TestCase):
    """直接对拍底层 iv_* / bm_* 原语，保证两条路径各自正确。"""

    def test_iv_primitives(self):
        rng = random.Random(7)
        for _ in range(300):
            U = rng.choice([10, 100, 1000])
            def rand_ivs():
                pts = sorted(rng.sample(range(U), rng.randint(0, min(U, 20))))
                return normalize_intervals(
                    [(p, p + rng.randint(0, 5)) for p in pts])
            a, b = rand_ivs(), rand_ivs()
            sa = {v for lo, hi in a for v in range(lo, hi + 1)}
            sb = {v for lo, hi in b for v in range(lo, hi + 1)}
            def toset(ivs):
                return {v for lo, hi in ivs for v in range(lo, hi + 1)}
            for r in (iv_union(a, b), iv_intersection(a, b),
                      iv_difference(a, b), iv_symmetric_difference(a, b)):
                assert_normalized(r)
            self.assertEqual(toset(iv_union(a, b)), sa | sb)
            self.assertEqual(toset(iv_intersection(a, b)), sa & sb)
            self.assertEqual(toset(iv_difference(a, b)), sa - sb)
            self.assertEqual(toset(iv_symmetric_difference(a, b)), sa ^ sb)
            self.assertEqual(iv_issubset(a, b), sa <= sb)

    def test_bm_primitives(self):
        rng = random.Random(11)
        for _ in range(300):
            U = rng.choice([1, 7, 64, 300, 2048])
            vals = random_subset(rng, U, rng.random())
            bits = bm_from_values(sorted(vals), U)
            self.assertEqual({i for i in range(U) if (bits >> i) & 1}, vals)
            ivs = bm_to_intervals(bits)
            assert_normalized(ivs)
            self.assertEqual(len(ivs), bm_run_count(bits))
            self.assertEqual({v for lo, hi in ivs for v in range(lo, hi + 1)}, vals)


class DifferentialFuzz(unittest.TestCase):
    def test_small_dense_random(self):
        rng = random.Random(2026)
        for _ in range(400):
            U = rng.choice([1, 2, 7, 64, 200, 1000, 4096])
            da, db = rng.random(), rng.random()
            a, b = random_subset(rng, U, da), random_subset(rng, U, db)
            A, B = BitSet.from_ints(a, U), BitSet.from_ints(b, U)
            check_all(self, A, B, a, b)

    def test_forced_reps_agree(self):
        """同一数据强制两种表示，运算结果都必须等于朴素 set。"""
        rng = random.Random(99)
        for _ in range(200):
            U = rng.choice([8, 100, 777, 4096])
            a = random_subset(rng, U, rng.random())
            b = random_subset(rng, U, rng.random())
            for force in (BitSet.BITMAP, BitSet.INTERVAL):
                A = BitSet.from_ints(a, U, force=force)
                B = BitSet.from_intervals(
                    [(v, v) for v in sorted(b)], U, force=force)
                A.assert_invariants()
                B.assert_invariants()
                check_all(self, A, B, a, b)
            # 混合表示
            A = BitSet.from_ints(a, U, force=BitSet.BITMAP)
            B = BitSet.from_ints(b, U, force=BitSet.INTERVAL)
            check_all(self, A, B, a, b)
            check_all(self, B, A, b, a)

    def test_huge_sparse(self):
        """超大上界（2^40，位图需 128 GiB 不可行）下的稀疏集合对拍。"""
        rng = random.Random(4242)
        U = 1 << 40
        for _ in range(20):
            a = {rng.randrange(U) for _ in range(rng.randint(0, 300))}
            b = {rng.randrange(U) for _ in range(rng.randint(0, 300))}
            # 混入若干长区间
            ivs = []
            for _ in range(rng.randint(0, 5)):
                lo = rng.randrange(U - 10 ** 6)
                ivs.append((lo, lo + rng.randint(0, 10 ** 6)))
            A = BitSet.from_ints(a, U)
            B = BitSet.from_intervals(ivs, U)
            sb = {v for lo, hi in ivs for v in range(lo, hi + 1)}
            self.assertEqual(A.kind, BitSet.INTERVAL)
            self.assertEqual(B.kind, BitSet.INTERVAL)
            check_all(self, A, B, a, sb)

    def test_interval_heavy_random(self):
        """区间构造 + 运算链，逐步对拍并断言不变量。"""
        rng = random.Random(31337)
        U = 5000
        cur = BitSet.empty(U)
        ref = set()
        for _ in range(100):
            lo = rng.randrange(U)
            hi = min(U - 1, lo + rng.randint(0, 200))
            N = BitSet.from_intervals([(lo, hi)], U)
            ns = set(range(lo, hi + 1))
            op = rng.choice("uidx")
            if op == "u":
                cur, ref = cur | N, ref | ns
            elif op == "i":
                cur, ref = cur & N, ref & ns
            elif op == "d":
                cur, ref = cur - N, ref - ns
            else:
                cur, ref = cur ^ N, ref ^ ns
            cur.assert_invariants()
            self.assertEqual(set(cur), ref)
            self.assertEqual(list(cur), sorted(ref))


if __name__ == "__main__":
    unittest.main(verbosity=1)
