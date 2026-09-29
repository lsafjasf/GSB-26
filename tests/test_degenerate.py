"""退化用例集：每种退化/边界输入单独一个用例。

运行: python3 -m unittest tests.test_degenerate -v
或:   python3 tests/test_degenerate.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maxflow.dinic import MaxFlow, max_flow
from maxflow.brute import brute_max_flow, brute_min_cut_capacity


def solve(n, edges, s, t):
    mf = MaxFlow(n)
    for edge in edges:
        mf.add_edge(*edge)
    value = mf.max_flow(s, t)
    cut = mf.min_cut()
    return mf, value, cut, mf.cut_capacity(cut)


class TestDegenerate(unittest.TestCase):

    def assertFlowCutEqual(self, n, edges, s, t):
        _, value, cut, cap = solve(n, edges, s, t)
        self.assertEqual(value, cap, "流值必须等于割容量")
        return value, cut, cap

    def test_source_equals_sink(self):
        # 源汇相同：流值恒 0，割为空
        edges = [(0, 1, 5), (1, 0, 7), (1, 2, 9)]
        value, cut, cap = self.assertFlowCutEqual(3, edges, 1, 1)
        self.assertEqual(value, 0)
        self.assertEqual(cut, [])
        self.assertEqual(cap, 0)

    def test_no_path(self):
        # s 到 t 完全不通
        edges = [(0, 1, 4), (2, 3, 4), (3, 2, 2)]
        value, cut, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, 0)
        # 最小割 S={0,1}，割边集合为空（不存在 S->T 的边）
        self.assertEqual([e.id for e in cut], [])

    def test_single_edge(self):
        edges = [(0, 1, 42)]
        value, cut, cap = self.assertFlowCutEqual(2, edges, 0, 1)
        self.assertEqual(value, 42)
        self.assertEqual([e.id for e in cut], [0])

    def test_antiparallel_edges(self):
        # 反平行边：0->1 容量 3，1->0 容量 7，互不抵消
        edges = [(0, 1, 3), (1, 0, 7), (1, 2, 10)]
        value, cut, cap = self.assertFlowCutEqual(3, edges, 0, 2)
        self.assertEqual(value, 3)
        self.assertEqual(cap, brute_min_cut_capacity(3, edges, 0, 2))
        self.assertEqual(brute_max_flow(3, edges, 0, 2), 3)

    def test_self_loops_ignored(self):
        # 自环不贡献流与割；带超大自环容量也不得干扰
        edges = [(0, 0, 10**60), (1, 1, 0), (0, 1, 8), (1, 1, 3)]
        value, cut, cap = self.assertFlowCutEqual(2, edges, 0, 1)
        self.assertEqual(value, 8)
        self.assertEqual([e.id for e in cut], [2])

    def test_zero_capacity_edges(self):
        # 零容量边不可增广；0->1 是唯一跨割边，其容量为 0
        edges = [(0, 1, 0), (0, 2, 6), (2, 1, 0), (1, 3, 5)]
        value, cut, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, 0)
        self.assertIn(0, [e.id for e in cut])
        self.assertEqual(cap, 0)

    def test_zero_source_out_edge_with_alternate(self):
        # 源点首条出边容量为 0，只能走另一条路
        edges = [(0, 1, 0), (0, 2, 5), (2, 3, 4), (1, 3, 100)]
        value, _, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, 4)
        self.assertEqual(value, brute_max_flow(4, edges, 0, 3))

    def test_huge_capacities_no_overflow(self):
        # 远超 64 位范围：Python 任意精度整数，结果精确
        BIG = 10**100
        edges = [(0, 1, BIG), (1, 2, BIG + 7), (0, 2, BIG // 3)]
        value, cut, cap = self.assertFlowCutEqual(3, edges, 0, 2)
        self.assertEqual(value, BIG + BIG // 3)
        self.assertEqual(value, BIG + BIG // 3)
        # 枚举割交叉验证
        self.assertEqual(cap, brute_min_cut_capacity(3, edges, 0, 2))

    def test_parallel_duplicate_edges(self):
        # 完全重边：容量独立累加
        edges = [(0, 1, 2), (0, 1, 3), (0, 1, 5), (1, 2, 9)]
        value, _, cap = self.assertFlowCutEqual(3, edges, 0, 2)
        self.assertEqual(value, 9)
        self.assertEqual(cap, brute_min_cut_capacity(3, edges, 0, 2))

    def test_complete_bipartite(self):
        # 完全二分图 K_{3,3}，左源右汇之外加超级源/超级汇
        # 顶点: 4=super source, 0..2=左部, 5..7=右部, 8=super sink
        n = 9
        s, t = 3, 8
        edges = []
        left = [0, 1, 2]
        right = [4, 5, 6]
        supply = [4, 7, 3]
        demand = [5, 2, 7]
        for i, u in enumerate(left):
            edges.append((s, u, supply[i]))
        for j, v in enumerate(right):
            edges.append((v, t, demand[j]))
        for u in left:
            for v in right:
                edges.append((u, v, 100))
        value, _, cap = self.assertFlowCutEqual(n, edges, s, t)
        # 受限于供给 14 与需求 14
        self.assertEqual(value, 14)
        self.assertEqual(cap, brute_min_cut_capacity(n, edges, s, t))
        self.assertEqual(value, brute_max_flow(n, edges, s, t))

    def test_extreme_capacity_ratio(self):
        # 容量相差 200 个数量级，结果仍精确，增广次数不依赖容量大小
        edges = [(0, 1, 1), (1, 3, 1),
                 (0, 2, 10**200), (2, 3, 10**200)]
        value, _, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, 10**200 + 1)

    def test_disconnected_isolated_vertices(self):
        edges = []
        value, cut, cap = self.assertFlowCutEqual(5, edges, 0, 4)
        self.assertEqual(value, 0)
        self.assertEqual(cut, [])

    def test_empty_graph_same_st(self):
        # 单顶点图
        r = max_flow(1, [], 0, 0)
        self.assertEqual(r.value, 0)
        self.assertEqual(r.cut, ())
        self.assertEqual(r.cut_capacity, 0)

    def test_reverse_flow_cancellation(self):
        # 需要“退回”已推送流量才能找到最优解的经典情形
        edges = [
            (0, 1, 10), (0, 2, 10),
            (1, 2, 1), (1, 3, 10),
            (2, 3, 10),
        ]
        value, _, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, 20)
        self.assertEqual(value, brute_max_flow(4, edges, 0, 3))

    def test_invalid_inputs(self):
        mf = MaxFlow(3)
        self.assertRaises(ValueError, mf.add_edge, 0, 3, 1)
        self.assertRaises(ValueError, mf.add_edge, -1, 0, 1)
        self.assertRaises(ValueError, mf.add_edge, 0, 1, -1)
        self.assertRaises(TypeError, mf.add_edge, 0, 1, 1.5)
        self.assertRaises(ValueError, MaxFlow, -1)
        mf.add_edge(0, 1, 1)
        self.assertRaises(RuntimeError, mf.min_cut)

    def test_repeated_and_incremental_solve(self):
        # 重复求解幂等；求解后加边再求解，返回累计流值
        mf = MaxFlow(3)
        mf.add_edge(0, 1, 5)
        mf.add_edge(1, 2, 5)
        self.assertEqual(mf.max_flow(0, 2), 5)
        self.assertEqual(mf.max_flow(0, 2), 5)
        e = mf.add_edge(0, 2, 3)
        self.assertEqual(mf.max_flow(0, 2), 8)
        self.assertEqual(mf.flow_on(e), 3)
        self.assertEqual(mf.cut_capacity(), 8)

    def test_cycle_network(self):
        # 多个环 + 反平行，Dinic 与暴力一致
        edges = [
            (0, 1, 6), (1, 0, 2), (1, 2, 3), (2, 1, 8),
            (2, 3, 4), (3, 2, 1), (1, 3, 2), (0, 2, 5),
        ]
        value, _, cap = self.assertFlowCutEqual(4, edges, 0, 3)
        self.assertEqual(value, brute_max_flow(4, edges, 0, 3))
        self.assertEqual(cap, brute_min_cut_capacity(4, edges, 0, 3))

    def test_current_arc_blocking_flow(self):
        # 当前弧优化回归用例（分层对抗网络）：
        # 宽走廊 0->1->...->w（容量 k）末端扇出 k 条单位容量车道 w->v_j->t。
        # 正确写法在 1 个相位内推出全部 k 单位阻塞流；游标提前前移的
        # 错误写法会退化成 k 个相位、每相位只推 1 条增广路。
        k, corridor = 20, 6
        edges = [(i, i + 1, k) for i in range(corridor)]
        w = corridor
        base = w + 1
        t = base + k
        n = t + 1
        for j in range(k):
            v = base + j
            edges.append((w, v, 1))
            edges.append((v, t, 1))

        mf = MaxFlow(n)
        for edge in edges:
            mf.add_edge(*edge)
        self.assertEqual(mf.max_flow(0, t), k)
        self.assertEqual(mf.phase_count, 1, "阻塞流必须在单个相位内推完")
        self.assertEqual(mf.cut_capacity(), k)
        self.assertEqual(brute_max_flow(n, edges, 0, t), k)

    def test_current_arc_partial_bottleneck_reuse(self):
        # 更一般的回归：一条宽弧后接两条不同瓶颈（1 和 3），共享上游宽弧。
        # 第一次增广沿瓶颈 1 的路推 1 单位后宽弧仍有残量，游标不能前移，
        # 同一相位须继续沿另一条路推 3 单位 —— 仍是 1 个相位。
        # 顶点: s=0, a=1；路 a->x=2->t=4（容量 1），路 a->y=3->t（容量 3）
        edges = [(0, 1, 10), (1, 2, 10), (2, 4, 1),
                 (1, 3, 10), (3, 4, 3)]
        mf = MaxFlow(5)
        for edge in edges:
            mf.add_edge(*edge)
        self.assertEqual(mf.max_flow(0, 4), 4)
        self.assertEqual(mf.phase_count, 1)
        self.assertEqual(mf.cut_capacity(), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
