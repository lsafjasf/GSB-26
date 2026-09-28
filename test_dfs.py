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


def reachable_unique_edges(g, start):
    """独立计算从 start 可达的唯一有向边数（BFS，不使用被测 DFS）。

    DFS 只遍历起点可达分量，故边不变量的正确口径是
    “可达唯一边数”，而不是全图唯一边数。
    """
    seen_nodes = {start}
    total = 0
    stack = [start]
    while stack:
        u = stack.pop()
        unique_neighbors = set(g.adj.get(u, ()))
        total += len(unique_neighbors)
        for v in unique_neighbors:
            if v not in seen_nodes:
                seen_nodes.add(v)
                stack.append(v)
    return total


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
        # 不变量2：从起点可达的每条唯一有向边恰好处理一次。
        # 注意口径不是 g.unique_edge_count()：不可达分量的边不会被遍历到。
        expected_edges = reachable_unique_edges(g, start)
        self.assertEqual(dfs.edges_processed, expected_edges)
        return order

    def test_unreachable_subgraph(self):
        """图含从起点不可达的分量时，只计可达唯一边，不拿全图口径比较。"""
        g = Graph()
        g.add_edge(0, 1)        # 可达分量：唯一边 (0,1)
        g.add_edge(2, 3)        # 不可达分量
        g.add_edge(3, 4)
        g.add_edge(3, 4)        # 不可达分量内的并行边
        order = self.assert_visit_once(g, 0)
        self.assertEqual(order, [0, 1])
        # 旧口径（全图唯一边数 = 3）必然误报，可达口径应为 1
        self.assertEqual(reachable_unique_edges(g, 0), 1)
        self.assertLess(reachable_unique_edges(g, 0), g.unique_edge_count())

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
        rng = random.Random(7)
        g = Graph()
        n, m = 2_000, 20_000
        for _ in range(m):
            g.add_edge(rng.randrange(n), rng.randrange(n))
        order = self.assert_visit_once(g, 0)
        # 种子 7 下图不连通：显式核对存在不可达节点与不可达边，
        # 防止再靠“固定种子恰好全图可达”把口径问题掩盖过去。
        self.assertLess(len(order), n)
        self.assertLess(reachable_unique_edges(g, 0),
                        g.unique_edge_count())


class TestExceptionCleanup(unittest.TestCase):
    class Boom(Exception):
        pass

    def make_raising_graph(self):
        """同一张图：可通过 raise_enabled 开关控制是否在遍历中途抛异常。"""
        g = Graph()
        g.add_edge(0, 1)
        g.add_edge(1, 2)
        g.add_edge(1, 3)
        outer = self
        g.raise_enabled = True

        class RaisingList(list):
            def __iter__(self):
                for i, v in enumerate(super().__iter__()):
                    if i == 1 and getattr(g, "raise_enabled", False):
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
        """同一张图：首次遍历中途抛异常，关闭抛错后用同一实例再次遍历，
        结果必须与全新实例在该图上的首次遍历一致（状态未被污染）。

        不能换图比对——换一张不抛异常的图无法验证异常路径残留的
        visited/visit_order 是否污染了后续遍历。
        """
        g = self.make_raising_graph()
        dfs = DFS(g)
        with self.assertRaises(self.Boom):
            dfs.traverse(0)
        # 同一张图，关闭抛错，复用同一个实例完成完整遍历
        g.raise_enabled = False
        again = dfs.traverse(0)
        again_edges = dfs.edges_processed
        fresh_dfs = DFS(g)
        fresh = fresh_dfs.traverse(0)
        self.assertEqual(again, fresh)
        self.assertEqual(again, [0, 1, 2, 3])
        self.assertEqual(again_edges, fresh_dfs.edges_processed)
        self.assertEqual(again_edges, reachable_unique_edges(g, 0))

        # 同一张图重新打开抛错：污染会让崩溃位置/表现改变；
        # 这里直接验证再次抛异常后状态依旧干净，且“异常-恢复”可重复。
        g.raise_enabled = True
        with self.assertRaises(self.Boom):
            dfs.traverse(0)
        self.assertEqual(dfs.visited, set())
        self.assertEqual(dfs.visit_order, [])
        self.assertEqual(dfs.edges_processed, 0)


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
