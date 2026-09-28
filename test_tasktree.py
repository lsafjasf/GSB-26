"""tasktree 自测：状态断言、资源断言、竞争时序、边界情形。

运行：python3 -m unittest test_tasktree -v
"""

import threading
import sys
import time
import unittest

from tasktree import (
    Cancelled,
    InvalidStateError,
    Resource,
    State,
    TaskNode,
)


def make_tree(depth: int, breadth: int, name: str = "root") -> TaskNode:
    root = TaskNode(name)
    root.start()
    frontier = [root]
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for i in range(breadth):
                child = node.create_child(f"{node.name}.{i}")
                child.start()
                nxt.append(child)
        frontier = nxt
    return root


def all_nodes(root: TaskNode):
    """显式栈先序遍历（迭代版），断言深链时自身也不依赖递归。"""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


class TestStates(unittest.TestCase):
    """状态机与取消/失败区分。"""

    def test_normal_lifecycle(self):
        t = TaskNode("t")
        self.assertEqual(t.state, State.PENDING)
        t.start()
        self.assertEqual(t.state, State.RUNNING)
        t.set_result(42)
        self.assertEqual(t.state, State.COMPLETED)
        self.assertEqual(t.result(), 42)

    def test_cancel_is_terminal_and_irreversible(self):
        t = TaskNode("t")
        t.start()
        t.cancel()
        self.assertEqual(t.state, State.CANCELLED)
        with self.assertRaises(Cancelled):
            t.set_result(1)
        with self.assertRaises(Cancelled):
            t.set_exception(ValueError("boom"))
        with self.assertRaises(Cancelled):
            t.result()
        self.assertEqual(t.state, State.CANCELLED)

    def test_cancel_vs_failure_are_distinct(self):
        a, b = TaskNode("a"), TaskNode("b")
        a.start(); b.start()
        a.cancel()
        b.set_exception(ValueError("boom"))
        self.assertEqual(a.state, State.CANCELLED)
        self.assertEqual(b.state, State.FAILED)
        self.assertIsInstance(a.error, Cancelled)
        self.assertIsInstance(b.error, ValueError)
        with self.assertRaises(ValueError):
            b.result()

    def test_cancelled_exception_counts_as_cancel_not_failure(self):
        t = TaskNode("t")
        t.start()
        t.set_exception(Cancelled("self-cancel via exception"))
        self.assertEqual(t.state, State.CANCELLED)

    def test_double_finish_rejected(self):
        t = TaskNode("t")
        t.start()
        t.set_result(1)
        with self.assertRaises(InvalidStateError):
            t.set_result(2)
        with self.assertRaises(InvalidStateError):
            t.set_exception(ValueError())

    def test_parent_summary_aggregates(self):
        root = make_tree(depth=2, breadth=2)  # 1+2+4 = 7 nodes
        nodes = list(all_nodes(root))
        nodes[1].cancel()                      # 取消一棵子树（3 个节点）
        nodes[4].set_exception(ValueError("x"))  # 另一棵子树的根失败
        for n in nodes:
            if not n.done:
                n.set_result("ok")
        s = root.summary()
        self.assertEqual(s["total"], 7)
        self.assertEqual(s["cancelled"], 3)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["completed"], 3)
        self.assertEqual(s["failures"][0]["error_type"], "ValueError")


class TestPropagation(unittest.TestCase):
    """取消传播与边界情形。"""

    def test_empty_tree(self):
        root = TaskNode("root")
        root.start()
        self.assertTrue(root.wait_children(timeout=1))
        root.cancel()
        self.assertEqual(root.state, State.CANCELLED)
        self.assertEqual(root.summary()["total"], 1)

    def test_deep_nesting_cancel_from_root(self):
        root = make_tree(depth=200, breadth=1)  # 201 层链
        root.cancel()
        for n in all_nodes(root):
            self.assertEqual(n.state, State.CANCELLED)

    def test_deep_chain_beyond_recursion_limit_uses_no_python_recursion(self):
        # 默认递归上限通常为 1000；旧的递归实现在 1500 层链上必抛
        # RecursionError。此处构造 2 倍上限以上的链并保持上限为默认值，
        # 验证取消传播与汇总遍历均为迭代实现、不受递归层数限制。
        limit = sys.getrecursionlimit()
        depth = limit * 2 + 100
        root = TaskNode("root")
        root.start()
        node = root
        for _ in range(depth):
            node = node.create_child("n")
            node.start()

        before = root.summary()
        self.assertEqual(before["total"], depth + 1)
        self.assertEqual(before["counts"][State.RUNNING.value], depth + 1)
        self.assertEqual(before["cancelled"], 0)

        self.assertTrue(root.cancel())

        after = root.summary()
        self.assertEqual(after["total"], depth + 1)
        self.assertEqual(after["cancelled"], depth + 1)
        self.assertTrue(all(n.state is State.CANCELLED
                            for n in all_nodes(root)))
        # 深链上重复取消同样安全（走同一套迭代传播）
        self.assertFalse(root.cancel())

    def test_cancel_root_cancels_everything(self):
        root = make_tree(depth=3, breadth=3)  # 40 个节点
        root.cancel()
        s = root.summary()
        self.assertEqual(s["total"], 40)
        self.assertEqual(s["cancelled"], 40)

    def test_cancel_single_subtree_only(self):
        root = make_tree(depth=1, breadth=2)
        left, right = root.children
        left.cancel()
        self.assertEqual(left.state, State.CANCELLED)
        self.assertEqual(right.state, State.RUNNING)
        self.assertEqual(root.state, State.RUNNING)

    def test_self_cancel(self):
        t = TaskNode("self")
        t.start()
        # 任务在执行体中自行取消
        t.cancel()
        self.assertEqual(t.state, State.CANCELLED)
        with self.assertRaises(Cancelled):
            t.set_result("late")

    def test_child_of_cancelled_parent_inherits_cancel(self):
        root = TaskNode("root")
        root.start()
        root.cancel()
        child = root.create_child("late-child")
        self.assertEqual(child.state, State.CANCELLED)
        grandchild = child.create_child("late-grandchild")
        self.assertEqual(grandchild.state, State.CANCELLED)

    def test_create_child_under_completed_parent_rejected(self):
        root = TaskNode("root")
        root.start()
        root.set_result(1)
        with self.assertRaises(InvalidStateError):
            root.create_child("x")

    def test_wait_children(self):
        root = TaskNode("root")
        root.start()
        kids = [root.create_child(f"k{i}") for i in range(5)]
        for k in kids:
            k.start()

        def finish():
            for k in kids:
                k.set_result(1)
        threading.Timer(0.05, finish).start()
        self.assertTrue(root.wait_children(timeout=5))
        self.assertTrue(all(k.state is State.COMPLETED for k in kids))


class TestResources(unittest.TestCase):
    """资源释放的确定性与幂等性。"""

    def test_resources_released_on_complete(self):
        t = TaskNode("t")
        t.start()
        r1 = t.add_resource(Resource("handle"))
        r2 = t.add_resource(Resource("buffer"))
        t.set_result(1)
        self.assertTrue(r1.released)
        self.assertTrue(r2.released)
        self.assertTrue(t.resources_released)

    def test_resources_released_on_cancel(self):
        t = TaskNode("t")
        t.start()
        r = t.add_resource(Resource("handle"))
        t.cancel()
        self.assertTrue(r.released)

    def test_resource_added_after_finish_released_immediately(self):
        t = TaskNode("t")
        t.start()
        t.set_result(1)
        r = t.add_resource(Resource("late"))
        self.assertTrue(r.released)

    def test_repeated_cancel_is_idempotent(self):
        t = TaskNode("t")
        t.start()
        calls = []
        r = t.add_resource(Resource("h", releaser=lambda: calls.append(1)))
        self.assertTrue(t.cancel())
        self.assertFalse(t.cancel())
        self.assertFalse(t.cancel())
        self.assertEqual(len(calls), 1)  # release 只执行一次
        self.assertTrue(r.released)

    def test_cancel_finished_task_is_noop(self):
        t = TaskNode("t")
        t.start()
        t.set_result(7)
        self.assertFalse(t.cancel())
        self.assertEqual(t.state, State.COMPLETED)  # 不被取消覆盖
        self.assertEqual(t.result(), 7)

    def test_cancel_failed_task_keeps_failure(self):
        t = TaskNode("t")
        t.start()
        t.set_exception(ValueError("boom"))
        self.assertFalse(t.cancel())
        self.assertEqual(t.state, State.FAILED)

    def test_subtree_cancel_releases_all_descendant_resources(self):
        root = make_tree(depth=2, breadth=2)
        resources = []
        for n in all_nodes(root):
            resources.append(n.add_resource(Resource(f"r@{n.name}")))
        root.cancel()
        self.assertTrue(all(r.released for r in resources))


class TestCallbacks(unittest.TestCase):
    """完成回调语义：取消后不得触发。"""

    def test_callback_fires_on_complete(self):
        fired = []
        t = TaskNode("t")
        t.start()
        t.on_complete(lambda n: fired.append(n.state))
        t.set_result(1)
        self.assertEqual(fired, [State.COMPLETED])

    def test_callback_not_fired_on_cancel(self):
        fired = []
        t = TaskNode("t")
        t.start()
        t.on_complete(lambda n: fired.append(n.state))
        t.cancel()
        self.assertEqual(fired, [])

    def test_callback_registered_after_cancel_never_fires(self):
        fired = []
        t = TaskNode("t")
        t.start()
        t.cancel()
        t.on_complete(lambda n: fired.append(n.state))
        self.assertEqual(fired, [])


class TestRaces(unittest.TestCase):
    """竞争时序：cancel 与 set_result 并发，结果只能二选一，且取消后无写回。"""

    def test_cancel_vs_set_result_race(self):
        for _ in range(2000):
            t = TaskNode("race")
            t.start()
            barrier = threading.Barrier(3)
            outcome = {}

            def writer():
                barrier.wait()
                try:
                    t.set_result("win")
                    outcome["writer"] = "ok"
                except Cancelled:
                    outcome["writer"] = "cancelled"

            def canceller():
                barrier.wait()
                outcome["cancelled_now"] = t.cancel()

            w = threading.Thread(target=writer)
            c = threading.Thread(target=canceller)
            w.start(); c.start()
            barrier.wait()
            w.join(); c.join()

            # 不变量 1：终态恰好是两者之一，且与 writer 结果一致
            if outcome["writer"] == "ok":
                self.assertEqual(t.state, State.COMPLETED)
                self.assertEqual(t.result(), "win")
            else:
                self.assertEqual(t.state, State.CANCELLED)
                with self.assertRaises(Cancelled):
                    t.result()
            # 不变量 2：无论谁赢，任务都已到达终态且资源已释放
            self.assertTrue(t.done)

    def test_cancel_vs_callback_race_no_fire_after_cancel(self):
        for _ in range(1000):
            t = TaskNode("cb-race")
            t.start()
            fired = []
            t.on_complete(lambda n: fired.append(n.state))
            barrier = threading.Barrier(3)

            def canceller():
                barrier.wait()
                t.cancel()

            def completer():
                barrier.wait()
                try:
                    t.set_result(1)
                except Cancelled:
                    pass

            threads = [threading.Thread(target=canceller),
                       threading.Thread(target=completer)]
            for th in threads: th.start()
            barrier.wait()
            for th in threads: th.join()

            if t.state is State.CANCELLED:
                self.assertEqual(fired, [])  # 取消后回调不得触发
            else:
                self.assertEqual(fired, [State.COMPLETED])

    def test_concurrent_cancel_idempotent(self):
        for _ in range(500):
            t = TaskNode("multi-cancel")
            t.start()
            calls = []
            t.add_resource(Resource("h", releaser=lambda: calls.append(1)))
            threads = [threading.Thread(target=t.cancel) for _ in range(8)]
            for th in threads: th.start()
            for th in threads: th.join()
            self.assertEqual(t.state, State.CANCELLED)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
