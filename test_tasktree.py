"""Tests for tasktree: state/resource assertions, race interleavings."""

import threading
import time
import unittest

from tasktree import (
    State,
    Summary,
    TaskCancelledError,
    TaskFailedError,
    TaskNode,
)


class FakeHandle:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def build_tree(depth, fanout, name="n"):
    root = TaskNode(name)
    level = [root]
    counter = 0
    for _ in range(depth):
        nxt = []
        for node in level:
            for _ in range(fanout):
                counter += 1
                nxt.append(node.spawn(f"n{counter}"))
        level = nxt
    return root


class TestBasicTree(unittest.TestCase):
    def test_empty_tree(self):
        root = TaskNode("root")
        summary = root.wait_all(timeout=1.0)  # no children: returns at once
        self.assertIsInstance(summary, Summary)
        self.assertEqual(summary.total, 1)   # summary covers the subtree (root)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.cancelled, 0)
        root.cancel()
        summary = root.wait_all(timeout=1.0)
        self.assertEqual(summary.cancelled, 1)

    def test_spawn_and_wait_all(self):
        root = TaskNode("root")
        children = [root.spawn(f"c{i}") for i in range(5)]
        for i, c in enumerate(children):
            c.complete(i)
        root.complete("root-done")
        summary = root.wait_all(timeout=1.0)
        self.assertEqual(summary.total, 6)
        self.assertEqual(summary.completed, 6)
        self.assertEqual([c.result for c in children], list(range(5)))

    def test_wait_returns_state(self):
        n = TaskNode("x")
        n.fail(ValueError("boom"))
        self.assertIs(n.wait(timeout=1.0), State.FAILED)

    def test_wait_timeout(self):
        n = TaskNode("x")
        with self.assertRaises(TimeoutError):
            n.wait(timeout=0.05)


class TestCancellation(unittest.TestCase):
    def test_cancel_propagates_to_whole_subtree(self):
        root = build_tree(depth=4, fanout=3)  # 121 nodes
        mid = root.children[1]
        mid.cancel()
        summary = root.summary()
        # mid subtree: 1 + 3 + 9 + 27 = 40 nodes cancelled
        self.assertEqual(summary.cancelled, 40)
        self.assertEqual(summary.total, 121)
        for child in root.children:
            if child is mid:
                self.assertTrue(child.cancelled)
            else:
                self.assertFalse(child.cancelled)  # siblings untouched

    def test_cancel_root_cancels_everything(self):
        root = build_tree(depth=3, fanout=4)
        root.cancel()
        summary = root.summary()
        self.assertEqual(summary.cancelled, summary.total)
        self.assertTrue(root.cancelled)

    def test_cancel_is_irreversible(self):
        n = TaskNode("x")
        n.cancel()
        self.assertFalse(n.complete("late"))
        self.assertFalse(n.fail(ValueError("late")))
        self.assertIsNone(n.result)
        self.assertTrue(n.cancelled)
        self.assertIsInstance(n.error, TaskCancelledError)

    def test_spawn_after_cancel_is_born_cancelled(self):
        root = TaskNode("root")
        root.cancel()
        child = root.spawn("late-child")
        self.assertTrue(child.cancelled)
        self.assertIsInstance(child.error, TaskCancelledError)
        self.assertFalse(child.complete(1))

    def test_self_cancel_from_worker_thread(self):
        root = TaskNode("root")
        worker_task = root.spawn("worker")
        cleanup = worker_task.spawn("cleanup")

        def work():
            # worker decides to cancel itself mid-flight
            worker_task.cancel_self()

        t = threading.Thread(target=work)
        t.start()
        t.join()
        self.assertTrue(worker_task.cancelled)
        self.assertTrue(cleanup.cancelled)  # propagation still applies
        self.assertFalse(root.cancelled)    # parent unaffected

    def test_deep_nesting_cancel_no_recursion_error(self):
        depth = 5000
        root = TaskNode("root")
        node = root
        for i in range(depth):
            node = node.spawn(f"d{i}")
        root.cancel()
        summary = root.summary()
        self.assertEqual(summary.cancelled, depth + 1)
        self.assertTrue(node.cancelled)


class TestCancelVsFailure(unittest.TestCase):
    def test_states_and_error_types_are_distinct(self):
        root = TaskNode("root")
        a = root.spawn("a")
        b = root.spawn("b")
        a.cancel()
        b.fail(ValueError("bad input"))
        root.complete()

        self.assertIs(a.state, State.CANCELLED)
        self.assertIs(b.state, State.FAILED)
        self.assertIsInstance(a.error, TaskCancelledError)
        self.assertIsInstance(b.error, ValueError)
        self.assertNotIsInstance(b.error, TaskCancelledError)

        # TaskFailedError wraps failures with task context
        wrapped = TaskFailedError(b, b.error)
        self.assertIs(wrapped.task, b)
        self.assertIsInstance(wrapped.cause, ValueError)

    def test_parent_aggregates_cancellations_and_failures(self):
        root = TaskNode("root")
        for i in range(3):
            root.spawn(f"cancel-{i}").cancel()
        for i in range(2):
            root.spawn(f"fail-{i}").fail(RuntimeError(f"e{i}"))
        ok = root.spawn("ok")
        ok.complete(42)
        root.complete()

        summary = root.wait_all(timeout=1.0)
        self.assertEqual(summary.cancelled, 3)
        self.assertEqual(summary.failed, 2)
        self.assertEqual(summary.completed, 2)
        self.assertEqual({n.name for n in summary.cancellations},
                         {"cancel-0", "cancel-1", "cancel-2"})
        self.assertEqual({n.name for n in summary.failures},
                         {"fail-0", "fail-1"})


class TestResources(unittest.TestCase):
    def test_resources_released_on_cancel(self):
        n = TaskNode("x")
        h1, h2 = FakeHandle(), FakeHandle()
        buf = bytearray(b"data")
        n.register_handle(h1)
        n.register_handle(h2)
        n.register_buffer(buf)
        n.cancel()
        self.assertEqual(h1.close_calls, 1)
        self.assertEqual(h2.close_calls, 1)
        self.assertEqual(len(buf), 0)
        self.assertTrue(n.resources_released)

    def test_repeated_cancel_releases_exactly_once(self):
        n = TaskNode("x")
        h = FakeHandle()
        n.register_handle(h)
        for _ in range(10):
            n.cancel()
        self.assertEqual(h.close_calls, 1)

    def test_cancel_after_complete_is_noop(self):
        n = TaskNode("x")
        h = FakeHandle()
        n.register_handle(h)
        self.assertTrue(n.complete("done"))
        self.assertEqual(h.close_calls, 1)  # released on completion
        n.cancel()                          # no-op
        n.cancel()
        self.assertIs(n.state, State.COMPLETED)
        self.assertEqual(n.result, "done")
        self.assertIsNone(n.error)
        self.assertEqual(h.close_calls, 1)  # not released twice

    def test_register_on_finished_task_raises(self):
        n = TaskNode("x")
        n.complete()
        with self.assertRaises(RuntimeError):
            n.register_handle(FakeHandle())
        with self.assertRaises(RuntimeError):
            n.register_buffer(bytearray())
        m = TaskNode("y")
        m.cancel()
        with self.assertRaises(RuntimeError):
            m.register_handle(FakeHandle())


class TestCallbacks(unittest.TestCase):
    def test_callback_fires_on_complete_and_fail_not_cancel(self):
        fired = []
        a, b, c = TaskNode("a"), TaskNode("b"), TaskNode("c")
        for n in (a, b, c):
            n.add_done_callback(lambda node: fired.append(node.name))
        a.complete(1)
        b.fail(ValueError("x"))
        c.cancel()
        self.assertEqual(sorted(fired), ["a", "b"])

    def test_callback_registered_after_cancel_never_fires(self):
        fired = []
        n = TaskNode("x")
        n.cancel()
        n.add_done_callback(lambda node: fired.append(node.name))
        self.assertEqual(fired, [])


class TestRaceTiming(unittest.TestCase):
    def test_deterministic_complete_wins_then_cancel_is_noop(self):
        # Force interleaving: complete() holds the lock, cancel() arrives
        # mid-finalize and must become a no-op.
        n = TaskNode("x")
        n.register_handle(FakeHandle())
        in_finalize = threading.Event()
        release = threading.Event()

        def hook(node):
            in_finalize.set()
            release.wait(timeout=5)

        n._before_finalize = hook
        t = threading.Thread(target=lambda: n.complete("r"))
        t.start()
        self.assertTrue(in_finalize.wait(timeout=5))
        cancel_done = threading.Event()

        def do_cancel():
            n.cancel()
            cancel_done.set()

        ct = threading.Thread(target=do_cancel)
        ct.start()
        time.sleep(0.05)  # cancel is blocked on the node lock
        self.assertFalse(cancel_done.is_set())
        release.set()
        t.join(timeout=5)
        ct.join(timeout=5)
        self.assertIs(n.state, State.COMPLETED)
        self.assertEqual(n.result, "r")

    def test_deterministic_cancel_wins_then_complete_writes_nothing(self):
        n = TaskNode("x")
        fired = []
        n.add_done_callback(lambda node: fired.append(node))
        n.cancel()
        self.assertFalse(n.complete("late-result"))
        self.assertFalse(n.fail(ValueError("late-error")))
        self.assertIsNone(n.result)
        self.assertEqual(fired, [])

    def test_no_writeback_after_cancel_from_worker(self):
        # Worker pattern: check-then-write. Even if the check passes,
        # complete() must lose to a concurrent cancel and write nothing.
        n = TaskNode("worker")
        proceed = threading.Event()
        checked = threading.Event()
        go = threading.Event()
        results = []

        def work():
            proceed.wait(timeout=5)
            if not n.cancelled:          # check passes...
                checked.set()
                go.wait(timeout=5)       # ...cancel lands in between...
                results.append(n.complete("r"))  # ...but write still loses

        t = threading.Thread(target=work)
        t.start()
        proceed.set()
        self.assertTrue(checked.wait(timeout=5))
        n.cancel()
        go.set()
        t.join(timeout=5)
        self.assertEqual(results, [False])
        self.assertIsNone(n.result)
        self.assertTrue(n.cancelled)

    def test_stress_concurrent_cancel_vs_complete(self):
        # 1000 rounds: exactly one side wins; invariants always hold.
        for i in range(1000):
            n = TaskNode(f"n{i}")
            h = FakeHandle()
            n.register_handle(h)
            fired = []
            n.add_done_callback(lambda node: fired.append(node))
            barrier = threading.Barrier(3)

            def do_complete():
                barrier.wait()
                n.complete("r")

            def do_cancel():
                barrier.wait()
                n.cancel()

            t1 = threading.Thread(target=do_complete)
            t2 = threading.Thread(target=do_cancel)
            t1.start()
            t2.start()
            barrier.wait()
            t1.join()
            t2.join()

            self.assertEqual(h.close_calls, 1, f"round {i}: double release")
            if n.state is State.COMPLETED:
                self.assertEqual(n.result, "r")
                self.assertEqual(len(fired), 1)
            else:
                self.assertIs(n.state, State.CANCELLED)
                self.assertIsNone(n.result)
                self.assertEqual(fired, [])
                self.assertIsInstance(n.error, TaskCancelledError)

    def test_stress_cancel_during_fanout(self):
        # Cancel the root while another thread is still spawning children:
        # every child ever created must end up cancelled (born-cancelled rule).
        for round_ in range(50):
            root = TaskNode("root")
            stop = threading.Event()

            def spawner():
                i = 0
                while not stop.is_set():
                    root.spawn(f"c{i}")
                    i += 1

            t = threading.Thread(target=spawner)
            t.start()
            time.sleep(0.001)
            root.cancel()
            stop.set()
            t.join(timeout=5)
            summary = root.summary()
            self.assertEqual(summary.cancelled, summary.total,
                             f"round {round_}: non-cancelled node leaked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
