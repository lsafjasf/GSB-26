"""test_degenerate.py — 退化与边界用例集（unittest，仅标准库）。

覆盖：反平行边、自环、零容量边、容量为零的源出边、超大容量、
源汇相同、无路径、单边、完全二分网络。
"""

import unittest

from maxflow import MaxFlow
from bruteforce import BruteForceMaxFlow

BIG = 10**18  # 超大容量，验证不溢出


def both_classes():
    return (MaxFlow, BruteForceMaxFlow)


class TestDegenerate(unittest.TestCase):

    def test_antiparallel_edges(self):
        # u->v 与 v->u 同时存在，且都承载流量
        for cls in both_classes():
            mf = cls(4)
            mf.add_edge(0, 1, 5)
            mf.add_edge(1, 2, 5)
            mf.add_edge(2, 1, 4)   # 与 1->2 反平行
            mf.add_edge(0, 2, 3)
            mf.add_edge(2, 3, 7)
            mf.add_edge(1, 3, 4)
            f = mf.max_flow(0, 3)
            # 源出边总容量 5+3=8 为上界；0->1->3(4)+0->1->2->3(1)+0->2->3(3)=8
            self.assertEqual(f, 8)
            cut, edges, _ = mf.min_cut(0, 3)
            self.assertEqual(cut, f)

    def test_antiparallel_cancel(self):
        # 反平行边构成环流可能性：结果仍须满足对偶
        for cls in both_classes():
            mf = cls(3)
            mf.add_edge(0, 1, 10)
            mf.add_edge(1, 0, 10)
            mf.add_edge(1, 2, 6)
            f = mf.max_flow(0, 2)
            self.assertEqual(f, 6)
            cut, _, _ = mf.min_cut(0, 2)
            self.assertEqual(cut, 6)

    def test_self_loops(self):
        for cls in both_classes():
            mf = cls(3)
            mf.add_edge(0, 0, 100)   # 源上自环
            mf.add_edge(1, 1, 50)    # 中间点自环
            mf.add_edge(2, 2, 7)     # 汇上自环
            mf.add_edge(0, 1, 3)
            mf.add_edge(1, 2, 4)
            f = mf.max_flow(0, 2)
            self.assertEqual(f, 3)
            cut, edges, _ = mf.min_cut(0, 2)
            self.assertEqual(cut, 3)
            self.assertEqual(edges, [(0, 1, 3)])

    def test_zero_capacity_edges(self):
        for cls in both_classes():
            mf = cls(4)
            mf.add_edge(0, 1, 0)     # 零容量
            mf.add_edge(0, 2, 5)
            mf.add_edge(2, 1, 0)     # 零容量
            mf.add_edge(2, 3, 5)
            mf.add_edge(1, 3, 0)
            f = mf.max_flow(0, 3)
            self.assertEqual(f, 5)
            cut, _, _ = mf.min_cut(0, 3)
            self.assertEqual(cut, 5)

    def test_zero_cap_source_outedge(self):
        # 源的唯一出边容量为 0 -> 流为 0，割为空集（容量 0）
        for cls in both_classes():
            mf = cls(3)
            mf.add_edge(0, 1, 0)
            mf.add_edge(1, 2, 9)
            f = mf.max_flow(0, 2)
            self.assertEqual(f, 0)
            cut, edges, S = mf.min_cut(0, 2)
            self.assertEqual(cut, 0)
            self.assertEqual(edges, [])

    def test_huge_capacities_no_overflow(self):
        for cls in both_classes():
            mf = cls(4)
            mf.add_edge(0, 1, BIG)
            mf.add_edge(0, 2, BIG)
            mf.add_edge(1, 3, BIG)
            mf.add_edge(2, 3, BIG)
            mf.add_edge(1, 2, BIG)
            f = mf.max_flow(0, 3)
            self.assertEqual(f, 2 * BIG)   # 2*10^18，int 精确
            cut, _, _ = mf.min_cut(0, 3)
            self.assertEqual(cut, 2 * BIG)

    def test_huge_bottleneck(self):
        # 大容量被小瓶颈截断
        mf = MaxFlow(3)
        mf.add_edge(0, 1, BIG)
        mf.add_edge(1, 2, 7)
        self.assertEqual(mf.max_flow(0, 2), 7)

    def test_same_source_sink(self):
        for cls in both_classes():
            mf = cls(2)
            mf.add_edge(0, 1, 5)
            mf.add_edge(1, 0, 5)
            f = mf.max_flow(0, 0)
            self.assertEqual(f, 0)
            cut, edges, _ = mf.min_cut(0, 0)
            self.assertEqual(cut, 0)
            self.assertEqual(edges, [])

    def test_no_path(self):
        for cls in both_classes():
            mf = cls(4)
            mf.add_edge(0, 1, 5)
            mf.add_edge(2, 3, 5)   # 与汇不连通
            f = mf.max_flow(0, 3)
            self.assertEqual(f, 0)
            cut, edges, _ = mf.min_cut(0, 3)
            self.assertEqual(cut, 0)
            self.assertEqual(edges, [])

    def test_single_edge(self):
        for cls in both_classes():
            mf = cls(2)
            mf.add_edge(0, 1, 42)
            f = mf.max_flow(0, 1)
            self.assertEqual(f, 42)
            cut, edges, _ = mf.min_cut(0, 1)
            self.assertEqual(cut, 42)
            self.assertEqual(edges, [(0, 1, 42)])

    def test_single_vertex(self):
        mf = MaxFlow(1)
        self.assertEqual(mf.max_flow(0, 0), 0)

    def test_complete_bipartite(self):
        # K_{p,q}：源接左部容量 a_i，右部接汇容量 b_j，中间边容量 INF
        # 最大流 = min(割) = sum min 结构，验证流=割
        import random
        rng = random.Random(7)
        for cls in both_classes():
            p, q = 4, 5
            n = p + q + 2
            s, t = n - 2, n - 1
            mf = cls(n)
            a = [rng.randint(0, 10) for _ in range(p)]
            b = [rng.randint(0, 10) for _ in range(q)]
            for i in range(p):
                mf.add_edge(s, i, a[i])
            for j in range(q):
                mf.add_edge(p + j, t, b[j])
            for i in range(p):
                for j in range(q):
                    mf.add_edge(i, p + j, 10**9)
            f = mf.max_flow(s, t)
            # 完全二分 + 中间无穷：最大流 = min over 子集，即
            # 总供给与总需求受中间无限连通 -> 实际等于
            # min( sum a, sum b, 任意组合 ) = min(sum a, sum b)
            self.assertEqual(f, min(sum(a), sum(b)))
            cut, _, _ = mf.min_cut(s, t)
            self.assertEqual(cut, f)

    def test_flow_value_equals_cut_capacity_random(self):
        # 对偶性抽查：流值 == 割容量
        import random
        rng = random.Random(99)
        for _ in range(300):
            n = rng.randint(2, 10)
            mf = MaxFlow(n)
            for _ in range(rng.randint(0, 20)):
                mf.add_edge(rng.randrange(n), rng.randrange(n),
                            rng.choice([0, 1, 5, 100, 10**15]))
            s, t = rng.randrange(n), rng.randrange(n)
            f = mf.max_flow(s, t)
            cut, _, _ = mf.min_cut(s, t)
            self.assertEqual(f, cut)


if __name__ == "__main__":
    unittest.main(verbosity=2)
