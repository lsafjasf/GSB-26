"""复现 + 回归测试（仅标准库 unittest）。

第一组：用 buggy_dfs.BuggyDFSWalker 稳定复现四类现网问题。
第二组：用 dfs.DFSWalker 验证修复与不变量。

运行：python3 -m unittest -v test_dfs.py
"""

import random
import sys
import unittest

from dfs import DFSWalker, Edge, Graph, TraversalAborted
from buggy_dfs import BuggyDFSWalker, BuggyMarkOnEntryWalker

CHAIN_N = 100_000


def chain_graph(n: int, directed: bool = True) -> Graph:
    g = Graph(directed=directed)
    g.add_node(0)
    for i in range(n - 1):
        g.add_edge(i, i, i + 1)
    return g


# ---------------------------------------------------------------------------
# 第一组：修复前 —— 四类问题均可稳定复现
# ---------------------------------------------------------------------------
class ReproduceBugsTest(unittest.TestCase):
    def test_bug1_deep_chain_stack_overflow(self):
        """十万节点链：递归实现必触发 RecursionError。"""
        walker = BuggyDFSWalker(chain_graph(CHAIN_N))
        with self.assertRaises(RecursionError):
            walker.walk(0)

    def test_bug2_cycle_revisits_nodes(self):
        """环 0->1->2->0：边 (2,0) 被处理超过一次（递归重复进入）。"""
        g = Graph(directed=True)
        g.add_edge("a", 0, 1)
        g.add_edge("b", 1, 2)
        g.add_edge("c", 2, 0)
        edge_hits = []
        walker = BuggyDFSWalker(g, on_edge=lambda e: edge_hits.append(e.id))
        # 递归在环上无限自我调用，最终 RecursionError，且同一 edge 被反复处理
        with self.assertRaises(RecursionError):
            walker.walk(0)
        self.assertGreater(edge_hits.count("a"), 1)

    def test_bug3_parallel_undirected_edge_processed_twice(self):
        """无向边应处理 1 次，缺陷实现处理 2 次（两个邻接表副本）。"""
        g = Graph(directed=False)
        g.add_edge("e1", 0, 1)
        edge_hits = []
        walker = BuggyMarkOnEntryWalker(
            g, on_edge=lambda e: edge_hits.append(e.id))
        walker.walk(0)
        self.assertEqual(edge_hits, ["e1", "e1"])  # 复现：同一边两次

    def test_bug4_exception_poisons_later_walk(self):
        """异常后 visited 残留：再次遍历时已完成子树的节点被跳过。"""
        g = Graph(directed=True)  # 星型 0 -> {1,2,3}
        g.add_edge("e1", 0, 1)
        g.add_edge("e2", 0, 2)
        g.add_edge("e3", 0, 3)

        first_hits, second_hits = [], []

        def boom_edge(edge):
            if edge.id == "e2":  # 处理到第二条边时中断（1 已完整访问并标记）
                raise TraversalAborted("boom")

        walker = BuggyDFSWalker(g, on_edge=boom_edge,
                                on_node=first_hits.append)
        with self.assertRaises(TraversalAborted):
            walker.walk(0)
        self.assertEqual(first_hits, [1])  # 缺陷版离开时回调：仅 1 完成

        # 异常之后再次遍历（不再抛异常）：节点 1 被残留标记跳过
        walker.on_edge = None
        walker.on_node = second_hits.append
        walker.walk(0)
        self.assertNotIn(1, second_hits)  # 复现：1 被污染状态吞掉

        # 对照：全新遍历器的正确结果应包含全部节点
        fresh_hits = []
        BuggyDFSWalker(g, on_node=fresh_hits.append).walk(0)
        self.assertEqual(sorted(fresh_hits), [0, 1, 2, 3])
        self.assertNotEqual(sorted(second_hits), sorted(fresh_hits))


# ---------------------------------------------------------------------------
# 第二组：修复后 —— 不变量与回归
# ---------------------------------------------------------------------------
class FixedInvariantsTest(unittest.TestCase):
    def test_100k_chain_iterative_no_recursion(self):
        """十万节点链：低递归上限下也能完整遍历（不依赖递归深度）。"""
        g = chain_graph(CHAIN_N)
        node_hits, edge_hits = [], []
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200)
        try:
            result = DFSWalker(
                g,
                on_node=node_hits.append,
                on_edge=lambda e: edge_hits.append(e.id),
            ).walk(0)
        finally:
            sys.setrecursionlimit(old_limit)
        self.assertEqual(len(node_hits), CHAIN_N)
        self.assertEqual(len(edge_hits), CHAIN_N - 1)
        self.assertEqual(node_hits, list(range(CHAIN_N)))
        self.assertTrue(result.completed)

    def test_cycle_nodes_visited_once(self):
        """环：每个可达节点恰好 1 次，每条边恰好 1 次。"""
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
        """无向边：从任一端点出发都只处理 1 次。"""
        for start in (0, 1):
            g = Graph(directed=False)
            g.add_edge("e1", 0, 1)
            edge_hits = []
            DFSWalker(g, on_edge=lambda e: edge_hits.append(e.id)).walk(start)
            self.assertEqual(edge_hits, ["e1"])

    def test_parallel_edges_each_processed_once(self):
        """并行边：相同两端点之间的 3 条边各处理 1 次，共 3 次。"""
        g = Graph(directed=True)
        for eid in ("p1", "p2", "p3"):
            g.add_edge(eid, 0, 1)
        g.add_edge("tail", 1, 2)
        edge_hits = []
        node_hits = []
        result = DFSWalker(
            g, on_node=node_hits.append,
            on_edge=lambda e: edge_hits.append(e.id)).walk(0)
        self.assertEqual(sorted(edge_hits), ["p1", "p2", "p3", "tail"])
        self.assertEqual(node_hits, [0, 1, 2])
        self.assertTrue(result.completed)

    def test_exception_clears_state_and_rewalk_identical(self):
        """异常路径清理状态：再次遍历 == 全新遍历（节点、边均一致）。"""
        g = chain_graph(4)
        boom = lambda node: (_ for _ in ()).throw(
            TraversalAborted("boom")) if node == 1 else None

        node_hits, edge_hits = [], []
        walker = DFSWalker(
            g, on_node=lambda n: (node_hits.append(n), boom(n)),
            on_edge=lambda e: edge_hits.append(e.id))

        with self.assertRaises(TraversalAborted):
            walker.walk(0)
        # 内部临时状态必须已清空
        self.assertTrue(walker.is_idle())
        self.assertEqual(node_hits, [0, 1])

        # 同一遍历器再次遍历（不再抛异常），结果必须与全新遍历一致
        node_hits2, edge_hits2 = [], []
        walker2 = DFSWalker(
            g, on_node=node_hits2.append,
            on_edge=lambda e: edge_hits2.append(e.id))
        walker.on_node = node_hits2.append
        walker.on_edge = lambda e: edge_hits2.append(e.id)
        re_result = walker.walk(0)
        fresh_result = walker2.walk(0)
        self.assertEqual(re_result.nodes, fresh_result.nodes)
        self.assertEqual(
            [e.id for e in re_result.edges],
            [e.id for e in fresh_result.edges])
        self.assertEqual(re_result.completed, fresh_result.completed, True)

    def test_exception_during_on_edge_also_cleans_up(self):
        """on_edge 抛异常同样清理，且已处理边只算到异常点。"""
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
        self.assertTrue(result.completed)
        self.assertEqual([e.id for e in edges], ["a", "b"])

    def test_traversal_order_is_deterministic(self):
        """邻接顺序 = (终点, edge_id) 升序；节点顺序 = 先序 DFS；可复现。"""
        g = Graph(directed=False)
        # 显式控制邻接表插入顺序
        g.add_edge("0-1", 0, 1)
        g.add_edge("0-2", 0, 2)
        g.add_edge("1-3", 1, 3)
        g.add_edge("3-2", 3, 2)  # 菱形回边：2 已被发现，不再入栈
        g.add_edge("0-4", 0, 4)
        expected_nodes = [0, 1, 3, 2, 4]
        expected_edges = ["0-1", "1-3", "3-2", "0-2", "0-4"]
        for _ in range(3):  # 多次重复，结果完全一致
            result = DFSWalker(g).walk(0)
            self.assertEqual(result.nodes, expected_nodes)
            self.assertEqual([e.id for e in result.edges], expected_edges)

    def test_adjacency_sorted_by_endpoint_then_edge_id(self):
        """邻接表按 (终点, edge_id) 升序，与插入先后无关。"""
        g = Graph(directed=True)
        # 故意以“乱序”插入：若按插入顺序遍历会先走 e9
        g.add_edge("e9", 0, 5)
        g.add_edge("e1", 0, 2)
        g.add_edge("e5", 0, 3)
        g.add_edge("e0", 2, 5)
        g.add_edge("e7", 3, 5)
        # 排序后 0 的邻接边为 (2,e1),(3,e5),(5,e9)
        self.assertEqual([e.id for e in g.edges_from(0)],
                         ["e1", "e5", "e9"])
        expected_nodes = [0, 2, 5, 3]
        expected_edges = ["e1", "e0", "e5", "e7", "e9"]
        for _ in range(3):  # 同图同配置，多次遍历逐条一致
            result = DFSWalker(g).walk(0)
            self.assertEqual(result.nodes, expected_nodes)
            self.assertEqual([e.id for e in result.edges], expected_edges)

    def test_adjacency_sort_is_insertion_order_independent(self):
        """同一批边以两种相反顺序插入，遍历结果完全一致。"""
        def build(edge_seq):
            g = Graph(directed=True)
            for eid, u, v in edge_seq:
                g.add_edge(eid, u, v)
            return g

        edges = [("a", 0, 2), ("b", 0, 1), ("c", 1, 3),
                 ("d", 2, 3), ("e", 0, 3)]
        r1 = DFSWalker(build(edges)).walk(0)
        r2 = DFSWalker(build(list(reversed(edges)))).walk(0)
        self.assertEqual(r1.nodes, r2.nodes)
        self.assertEqual([e.id for e in r1.edges],
                         [e.id for e in r2.edges])

    def test_duplicate_edge_id_rejected(self):
        """重复 edge_id 直接抛 ValueError（边身份无歧义）。"""
        g = Graph(directed=True)
        g.add_edge("x", 0, 1)
        with self.assertRaises(ValueError):
            g.add_edge("x", 1, 2)
        with self.assertRaises(ValueError):
            g.add_edge("x", 0, 1)  # 同端点同 id 同样拒绝

    def test_self_loop_processed_once(self):
        """自环：有向/无向都只存一份、恰好处理一次。"""
        for directed in (True, False):
            g = Graph(directed=directed)
            g.add_edge("loop", 0, 0)
            g.add_edge("out", 0, 1)
            edge_hits = []
            result = DFSWalker(
                g, on_edge=lambda e: edge_hits.append(e.id)).walk(0)
            self.assertEqual(edge_hits, ["loop", "out"])
            self.assertEqual(result.nodes, [0, 1])
            self.assertTrue(result.completed)
            self.assertEqual(len(g.edges_from(0)),
                             2)  # 无向自环不生成反向副本

    def test_random_graph_invariants(self):
        """随机有向多重图：节点访问次数 == 可达节点数；边次数 == 边总数。"""
        rng = random.Random(20260926)
        n_nodes = 300
        g = Graph(directed=True)
        for n in range(n_nodes):
            g.add_node(n)
        edges = [(i, i + 1) for i in range(n_nodes - 1)]  # 主干保证全可达
        while len(edges) < 1500:
            edges.append((rng.randrange(n_nodes), rng.randrange(n_nodes)))
        m = len(edges)
        for eid, (u, v) in enumerate(edges):
            g.add_edge(eid, u, v)  # 允许自环与并行结构
        node_hits, edge_hits = [], []
        result = DFSWalker(
            g, on_node=node_hits.append,
            on_edge=lambda e: edge_hits.append(e.id)).walk(0)
        self.assertEqual(len(node_hits), len(set(node_hits)))  # 无重复节点
        self.assertEqual(len(edge_hits), len(set(edge_hits)))  # 无重复边
        self.assertEqual(len(edge_hits), m)  # 有向图所有边都被遍历到
        self.assertTrue(result.completed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
