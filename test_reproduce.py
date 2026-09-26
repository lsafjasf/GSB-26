"""复现测试：稳定触发缺陷版 dfs_buggy 的四类现网问题。

运行：python3 test_reproduce.py
每个用例断言“缺陷行为确实发生”，即复现成功。
"""
import sys
import unittest

from dfs_buggy import BuggyGraph, BuggyDFS

DEEP_CHAIN = 100_000  # 十万节点链


def build_chain(n):
    g = BuggyGraph()
    for i in range(n - 1):
        g.add_edge(i, i + 1)
    return g


class ReproduceBugs(unittest.TestCase):
    def test_bug1_deep_chain_stack_overflow(self):
        """缺陷1：十万节点链式图，递归 DFS 栈溢出。"""
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(10_000)  # 模拟现网默认栈规模
        try:
            dfs = BuggyDFS(build_chain(DEEP_CHAIN))
            with self.assertRaises(RecursionError):
                dfs.traverse(0)
        finally:
            sys.setrecursionlimit(old_limit)

    def test_bug2_cycle_duplicate_visit(self):
        """缺陷2：环 1->2->1 导致节点 1 被重复访问。"""
        g = BuggyGraph()
        g.add_edge(1, 2)
        g.add_edge(2, 1)
        order = BuggyDFS(g).traverse(1)
        self.assertEqual(order, [1, 2, 1])          # 1 被访问两次
        self.assertNotEqual(len(order), len(set(order)))

    def test_bug3_parallel_edges_processed_repeatedly(self):
        """缺陷3：并行边 (1,2) x3 被处理 3 次而非 1 次。"""
        g = BuggyGraph()
        for _ in range(3):
            g.add_edge(1, 2)
        dfs = BuggyDFS(g)
        dfs.traverse(1)
        self.assertEqual(dfs.edges_processed, 3)     # 期望唯一边数 1，实际 3

    def test_bug4_exception_pollutes_later_traversal(self):
        """缺陷4：异常路径不清理 visited，后续遍历结果被污染。"""
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(10_000)
        try:
            g = build_chain(DEEP_CHAIN)
            dfs = BuggyDFS(g)
            with self.assertRaises(RecursionError):
                dfs.traverse(0)                      # 异常，visited 残留
            self.assertGreater(len(dfs.visited), 0)  # 状态未清理
            # 同一实例再次遍历，污染体现在两处：
            # (a) visit_order 残留上次崩溃前的 ~10000 个节点；
            # (b) 从节点 5 出发正常应访问 99995 个节点，
            #     但邻居 4、6 已在残留 visited 中，遍历立即终止。
            polluted = dfs.traverse(5)
            self.assertIn(5, dfs.visited)            # 起点早已在残留集合中
            self.assertEqual(polluted[-1], 5)          # 本次只访问了起点
            self.assertLess(len(polluted), 10_001)     # 远少于应有的 99995
            self.assertNotEqual(polluted, list(range(5, DEEP_CHAIN)))
        finally:
            sys.setrecursionlimit(old_limit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
