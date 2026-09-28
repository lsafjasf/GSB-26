"""复现 + 回归 + 顺序/去重/规模测试（仅标准库 unittest）。

第一组：buggy_dfs 稳定复现四类历史缺陷。
第二组：修复后不变量（不递归 / 节点边各一次 / 异常清理）。
第三组：稳定遍历顺序与去重策略（本迭代重点）。
第四组：规模（十万节点深链）。

运行：python3 -m unittest -v test_dfs.py
"""

import random
import sys
import unittest

from dfs import (
    DFSWalker,
    Edge,
    Graph,
    OrderPolicy,
    TraversalAborted,
)
from buggy_dfs import BuggyDFSWalker, BuggyMarkOnEntryWalker
from verify import (
    check_against_reference,
    reference_walk,
    reproducibility_check,
    sequence_hashes,
)

CHAIN_N = 100_000


def chain_graph(n: int, directed: bool = True,
                policy: str = "multigraph") -> Graph:
    g = Graph(directed=directed, duplicate_policy=policy)
    g.add_node(0)
    for i in range(n - 1):
        g.add_edge(i, i, i + 1)
    return g


# ---------------------------------------------------------------------------
# 第一组：历史缺陷复现
# ---------------------------------------------------------------------------
class ReproduceBugsTest(unittest.TestCase):
    def test_bug1_deep_chain_stack_overflow(self):
        walker = BuggyDFSWalker(chain_graph(CHAIN_N))
        with self.assertRaises(RecursionError):
            walker.walk(0)

    def test_bug2_cycle_revisits_nodes(self):
        g = Graph(directed=True)
        g.add_edge("a", 0, 1)
        g.add_edge("b", 1, 2)
        g.add_edge("c", 2, 0)
        edge_hits = []
        walker = BuggyDFSWalker(g, on_edge=lambda e: edge_hits.append(e.id))
        with self.assertRaises(RecursionError):
            walker.walk(0)
        self.assertGreater(edge_hits.count("a"), 1)

    def test_bug3_parallel_undirected_edge_processed_twice(self):
        g = Graph(directed=False)
        g.add_edge("e1", 0, 1)
        edge_hits = []
        walker = BuggyMarkOnEntryWalker(
            g, on_edge=lambda e: edge_hits.append(e.id))
        walker.walk(0)
        self.assertEqual(edge_hits, ["e1", "e1"])

    def test_bug4_exception_poisons_later_walk(self):
        g = Graph(directed=True)
        g.add_edge("e1", 0, 1)
        g.add_edge("e2", 0, 2)
        g.add_edge("e3", 0, 3)
        first_hits, second_hits = [], []

        def boom_edge(edge):
            if edge.id == "e2":
                raise TraversalAborted("boom")

        walker = BuggyDFSWalker(g, on_edge=boom_edge,
                                on_node=first_hits.append)
        with self.assertRaises(TraversalAborted):
            walker.walk(0)
        self.assertEqual(first_hits, [1])
        walker.on_edge = None
        walker.on_node = second_hits.append
        walker.walk(0)
        self.assertNotIn(1, second_hits)
        fresh_hits = []
        BuggyDFSWalker(g, on_node=fresh_hits.append).walk(0)
        self.assertEqual(sorted(fresh_hits), [0, 1, 2, 3])
        self.assertNotEqual(sorted(second_hits), sorted(fresh_hits))


# ---------------------------------------------------------------------------
# 第二组：修复后核心不变量
# ---------------------------------------------------------------------------
class FixedInvariantsTest(unittest.TestCase):
    def test_100k_chain_iterative_no_recursion(self):
        g = chain_graph(CHAIN_N)
        node_hits, edge_hits = [], []
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200)
        try:
            result = DFSWalker(
                g, on_node=node_hits.append,
                on_edge=lambda e: edge_hits.append(e.id)).walk(0)
        finally:
            sys.setrecursionlimit(old_limit)
        self.assertEqual(len(node_hits), CHAIN_N)
        self.assertEqual(len(edge_hits), CHAIN_N - 1)
        self.assertEqual(node_hits, list(range(CHAIN_N)))
        self.assertTrue(result.completed)

    def test_cycle_nodes_visited_once(self):
        g = Graph(directed=True)
        g.add_edge("a", 0, 1)
        g.add_edge("b", 1, 2)
        g.add_edge("c", 2, 0)
        node_hits, edge_hits = [], []
        result = DFSWalker(
            g, on_node=node_hits.append,
            on_edge=lambda e: edge_hits.append(e.id)).walk(0)
        self.assertEqual(sorted(node_hits), [0, 1, 2])
        self.assertEqual(sorted(edge_hits), ["a", "b", "c"])
        self.assertEqual(node_hits.count(0), 1)
        self.assertTrue(result.completed)

    def test_undirected_edge_processed_once_from_either_end(self):
        for start in (0, 1):
            g = Graph(directed=False)
            g.add_edge("e1", 0, 1)
            edge_hits = []
            DFSWalker(g, on_edge=lambda e: edge_hits.append(e.id)).walk(start)
            self.assertEqual(edge_hits, ["e1"])

    def test_parallel_edges_each_processed_once(self):
        g = Graph(directed=True)
        for eid in ("p1", "p2", "p3"):
            g.add_edge(eid, 0, 1)
        g.add_edge("tail", 1, 2)
        edge_hits, node_hits = [], []
        result = DFSWalker(
            g, on_node=node_hits.append,
            on_edge=lambda e: edge_hits.append(e.id)).walk(0)
        self.assertEqual(sorted(edge_hits), ["p1", "p2", "p3", "tail"])
        self.assertEqual(node_hits, [0, 1, 2])
        self.assertTrue(result.completed)

    def test_exception_clears_state_and_rewalk_identical(self):
        g = chain_graph(4)
        boom = lambda node: (_ for _ in ()).throw(
            TraversalAborted("boom")) if node == 1 else None
        node_hits, edge_hits = [], []
        walker = DFSWalker(
            g, on_node=lambda n: (node_hits.append(n), boom(n)),
            on_edge=lambda e: edge_hits.append(e.id))
        with self.assertRaises(TraversalAborted):
            walker.walk(0)
        self.assertTrue(walker.is_idle())
        self.assertEqual(node_hits, [0, 1])
        node_hits2, edge_hits2 = [], []
        walker2 = DFSWalker(
            g, on_node=node_hits2.append,
            on_edge=lambda e: edge_hits2.append(e.id))
        walker.on_node = node_hits2.append
        walker.on_edge = lambda e: edge_hits2.append(e.id)
        re_result = walker.walk(0)
        fresh_result = walker2.walk(0)
        self.assertEqual(re_result.nodes, fresh_result.nodes)
        self.assertEqual([e.id for e in re_result.edges],
                         [e.id for e in fresh_result.edges])
        self.assertTrue(re_result.completed)

    def test_exception_during_on_edge_also_cleans_up(self):
        g = Graph(directed=True)
        g.add_edge("a", 0, 1)
        g.add_edge("b", 1, 2)

        def on_edge(edge):
            if edge.id == "b":
                raise TraversalAborted("edge boom")

        walker = DFSWalker(g, on_edge=on_edge)
        with self.assertRaises(TraversalAborted):
            walker.walk(0)
        self.assertTrue(walker.is_idle())
        edges = []
        walker.on_edge = edges.append
        result = walker.walk(0)
        self.assertEqual([e.id for e in result.edges], ["a", "b"])
        self.assertEqual([e.id for e in edges], ["a", "b"])
        self.assertTrue(result.completed)


# ---------------------------------------------------------------------------
# 第三组：稳定顺序 + 去重策略（本迭代重点）
# ---------------------------------------------------------------------------
class OrderAndDedupTest(unittest.TestCase):
    def _diamond(self):
        # 菱形：0->{1,2,4}, 1->3, 3->2；含自环与并行边
        g = Graph(directed=False)
        g.add_edge("0-1", 0, 1)
        g.add_edge("0-2", 0, 2)
        g.add_edge("1-3", 1, 3)
        g.add_edge("3-2", 3, 2)
        g.add_edge("0-4", 0, 4)
        return g

    def test_sorted_diamond_order_matches_rule(self):
        g = self._diamond()
        result = DFSWalker(g).walk(0)
        # 邻接按 (目标键, 边id键)：0 的邻居 1<2<4；1 的邻居 0(已发现)->3
        self.assertEqual(result.nodes, [0, 1, 3, 2, 4])
        self.assertEqual([e.id for e in result.edges],
                         ["0-1", "1-3", "3-2", "0-2", "0-4"])

    def test_order_independent_of_insertion_order(self):
        """打乱 add_edge 录入顺序，SORTED 下节点序/边序逐条不变。"""
        edge_list = [("0-1", 0, 1), ("0-2", 0, 2), ("1-3", 1, 3),
                     ("3-2", 3, 2), ("0-4", 0, 4)]
        base = DFSWalker(self._diamond()).walk(0)

        def build(order):
            g = Graph(directed=False)
            for eid, u, v in order:
                g.add_edge(eid, u, v)
            return DFSWalker(g).walk(0)

        rng = random.Random(7)
        for _ in range(5):
            shuffled = edge_list[:]
            rng.shuffle(shuffled)
            r = build(shuffled)
            self.assertEqual(r.nodes, base.nodes)
            self.assertEqual([e.id for e in r.edges],
                             [e.id for e in base.edges])

    def test_insertion_policy_preserves_add_order(self):
        """INSERTION 策略：邻接顺序即录入顺序（旧行为）。"""
        g = Graph(directed=True, order=OrderPolicy.INSERTION)
        g.add_edge("a", 0, 2)
        g.add_edge("b", 0, 1)  # 录入时 2 在 1 前
        r = DFSWalker(g).walk(0)
        self.assertEqual(r.nodes, [0, 2, 1])
        self.assertEqual([e.id for e in r.edges], ["a", "b"])

    def test_self_loop_stored_once(self):
        for directed in (True, False):
            g = Graph(directed=directed)
            g.add_edge("loop", 0, 0)
            g.add_edge("out", 0, 1)
            r = DFSWalker(g).walk(0)
            self.assertEqual([e.id for e in r.edges].count("loop"), 1)
            self.assertEqual(
                [e.id for e in g.edges_from(0)].count("loop"), 1)

    def test_duplicate_edge_id_rejected(self):
        g = Graph(directed=True)
        g.add_edge("x", 0, 1)
        with self.assertRaises(ValueError):
            g.add_edge("x", 1, 2)

    def test_simple_policy_folds_parallel_edges(self):
        g = Graph(directed=True, duplicate_policy="simple")
        g.add_edge("p_a", 0, 1)
        g.add_edge("p_b", 0, 1)  # 并行边折叠
        g.add_edge("tail", 1, 2)
        r = DFSWalker(g).walk(0)
        self.assertEqual([e.id for e in r.edges], ["p_a", "tail"])
        self.assertEqual(g.num_edges, 2)

    def test_simple_policy_folds_self_loop_and_undirected(self):
        g = Graph(directed=False, duplicate_policy="simple")
        g.add_edge("l1", 0, 0)
        g.add_edge("l2", 0, 0)  # 自环折叠为一条
        g.add_edge("ab", 0, 1)
        g.add_edge("ab2", 1, 0)  # 无向反向对折叠
        r = DFSWalker(g).walk(0)
        ids = [e.id for e in r.edges]
        self.assertEqual(sorted(ids), ["ab", "l1"])
        self.assertEqual(g.num_edges, 2)

    def test_walk_all_source_order_is_sorted(self):
        """全图遍历：源按节点键升序；含两个不连通分量。"""
        g = Graph(directed=True)
        g.add_edge("a", 5, 6)
        g.add_edge("b", 1, 2)
        r = DFSWalker(g).walk(None)
        self.assertEqual(r.nodes, [1, 2, 5, 6])

    def test_deterministic_hashes_three_runs(self):
        g = self._diamond()
        hashes = [sequence_hashes(*(
            (lambda r: (r.nodes, r.edges))(DFSWalker(g).walk(0))))
            for _ in range(3)]
        self.assertTrue(all(h == hashes[0] for h in hashes))

    def test_matches_independent_reference_on_random_graph(self):
        rng = random.Random(20260928)
        n = 200
        for directed in (True, False):
            for policy in ("multigraph", "simple"):
                g = Graph(directed=directed, duplicate_policy=policy)
                for node in range(n):
                    g.add_node(node)
                for eid in range(1200):
                    g.add_edge(eid, rng.randrange(n), rng.randrange(n))
                ok, msgs, _ = check_against_reference(g, start=0)
                self.assertTrue(ok, msgs)

    def test_reproducibility_shuffled_insertion(self):
        n = 500

        def make_edges():
            rng = random.Random(4242)
            edges = [(i, i + 1) for i in range(n - 1)]
            edges += [(rng.randrange(n), rng.randrange(n))
                      for _ in range(2000)]
            return edges

        ok, detail = reproducibility_check(
            make_edges, n, directed=True, policy="multigraph", seed=99)
        self.assertTrue(ok, detail)
        self.assertEqual(detail["insertion_order_hashes"],
                         detail["shuffled_order_hashes"])


# ---------------------------------------------------------------------------
# 第四组：规模（十万节点深链，参照实现也完整跑一遍）
# ---------------------------------------------------------------------------
class ScaleChainTest(unittest.TestCase):
    def test_100k_chain_order_equals_reference(self):
        g = chain_graph(CHAIN_N)
        ref_nodes, ref_edges = reference_walk(g, 0)
        self.assertEqual(ref_nodes, list(range(CHAIN_N)))
        self.assertEqual(ref_edges, list(range(CHAIN_N - 1)))
        result = DFSWalker(g).walk(0)
        self.assertEqual(result.nodes, ref_nodes)
        self.assertEqual([e.id for e in result.edges], ref_edges)


if __name__ == "__main__":
    unittest.main(verbosity=2)
