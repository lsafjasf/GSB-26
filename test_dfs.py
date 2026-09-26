"""回归测试：修复版 dfs_fixed 的正确性与不变量断言。

运行：python3 test_dfs.py
"""
import random
import unittest

from dfs_fixed import Graph, DFS

DEEP_CHAIN = 100_000  # 十万节点链


def build_chain(n, cls=Graph):
    g = cls()
    for i in range(n - 1):
        g.add_edge(i, i + 1)
    return g


class TestNoRecursionLimit(unittest.TestCase):
    def test_deep_chain_100k(self):
        """十万节点链：不触发 RecursionError，顺序严格为 0..n-1。"""
        order = DFS(build_chain(DEEP_CHAIN)).traverse(0)
        self.assertEqual(order, list(range(DEEP_CHAIN)))

    def test_default_recursion_limit_untouched(self):
        """遍历不修改、也不依赖递归深度限制。"""
        import sys
        before = sys.getrecursionlimit()
        DFS(build_chain(DEEP_CHAIN)).traverse(0)
        self.assertEqual(sys.getrecursionlimit(), before)


class TestVisitOnceInvariant(unittest.TestCase):
    def assert_visit_once(self, g, start):
        dfs = DFS(g)
        order = dfs.traverse(start)
        # 不变量1：每个可达节点恰好访问一次
        self.assertEqual(len(order), len(set(order)))
        # 不变量2：每条唯一有向边恰好处理一次
        self.assertEqual(dfs.edges_processed, g.unique_edge_count())
        return order

    def test_cycle(self):
        g = Graph()
        g.add_edge(1, 2)
        g.add_edge(2, 3)
        g.add_edge(3, 1)  # 环
        self.assertEqual(self.assert_visit_once(g, 1), [1, 2, 3])

    def test_diamond(self):
        g = Graph()
        g.add_edge(1, 2)
        g.add_edge(1, 3)
        g.add_edge(2, 4)
        g.add_edge(3, 4)  # 菱形，4 从两条路径可达
        self.assertEqual(self.assert_visit_once(g, 1), [1, 2, 4, 3])

    def test_parallel_edges(self):
        g = Graph()
        for _ in range(3):
            g.add_edge(1, 2)  # 并行边 x3
        g.add_edge(2, 3)
        g.add_edge(2, 3)      # 并行边 x2
        order = self.assert_visit_once(g, 1)
        self.assertEqual(order, [1, 2, 3])
        dfs = DFS(g)
        dfs.traverse(1)
        self.assertEqual(dfs.edges_processed, 2)  # 唯一边 (1,2)、(2,3)

    def test_random_graph_invariants(self):
        """随机稠密图（含环与并行边）上的不变量。"""
        rng = random.Random(42)
        g = Graph()
        n, m = 2_000, 20_000
        for _ in range(m):
            g.add_edge(rng.randrange(n), rng.randrange(n))
        self.assert_visit_once(g, 0)


class TestExceptionCleanup(unittest.TestCase):
    class Boom(Exception):
        pass

    def make_raising_graph(self):
        """邻居迭代到一半抛异常的图。"""
        g = Graph()
        g.add_edge(0, 1)
        g.add_edge(1, 2)
        g.add_edge(1, 3)
        outer = self

        class RaisingList(list):
            def __iter__(self):
                for i, v in enumerate(super().__iter__()):
                    if i == 1:
                        raise outer.Boom("mid-traversal failure")
                    yield v

        g.adj[1] = RaisingList(g.adj[1])
        return g

    def test_state_reset_after_exception(self):
        g = self.make_raising_graph()
        dfs = DFS(g)
        with self.assertRaises(self.Boom):
            dfs.traverse(0)
        # 异常后实例状态必须复位
        self.assertEqual(dfs.visited, set())
        self.assertEqual(dfs.visit_order, [])

    def test_traversal_after_exception_matches_fresh(self):
        """异常后再次遍历，结果与全新实例首次遍历一致。"""
        g = self.make_raising_graph()
        dfs = DFS(g)
        with self.assertRaises(self.Boom):
            dfs.traverse(0)
        # 换一个不抛异常的图，同一实例复用
        g2 = Graph()
        g2.add_edge(0, 1)
        g2.add_edge(1, 2)
        g2.add_edge(0, 3)
        dfs.graph = g2
        again = dfs.traverse(0)
        fresh = DFS(g2).traverse(0)
        self.assertEqual(again, fresh)
        self.assertEqual(again, [0, 1, 2, 3])


class TestDeterministicOrder(unittest.TestCase):
    def test_insertion_order_and_preorder(self):
        """邻接按插入序展开，节点为 DFS 前序；重复运行结果一致。"""
        g = Graph()
        g.add_edge(0, 2)
        g.add_edge(0, 1)   # 邻接顺序为 [2, 1]
        g.add_edge(2, 3)
        g.add_edge(1, 3)
        expected = [0, 2, 3, 1]
        for _ in range(5):
            self.assertEqual(DFS(g).traverse(0), expected)

    def test_instance_reusable(self):
        """同一实例连续遍历结果一致。"""
        g = build_chain(1_000)
        dfs = DFS(g)
        first = dfs.traverse(0)
        second = dfs.traverse(0)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
