"""Memory / reclamation test.

Evidence that deleted nodes are reclaimed: run 100k insert+delete cycles on
a bounded key window, then show that
  1. the list's logical size stays bounded,
  2. live _Node objects (tracked via weakrefs) fall back to ~list size,
  3. process memory (tracemalloc) after the churn matches the baseline.
"""

import gc
import random
import tracemalloc
import unittest
import weakref

from skiplist import SkipList, _Node

CYCLES = 100_000
WINDOW = 1_000  # live working set stays <= WINDOW keys


class TestNodeReclamation(unittest.TestCase):
    def test_nodes_reclaimed_after_100k_cycles(self):
        gc.collect()
        before_nodes = sum(1 for o in gc.get_objects() if isinstance(o, _Node))

        sl = SkipList(rand=random.Random(2026).random)
        live_refs: weakref.WeakSet = weakref.WeakSet()

        # capture references to nodes as they are created, without keeping
        # them alive (WeakSet entries vanish once the node is collected)
        orig_insert = sl.insert

        def tracking_insert(k, v):
            created = orig_insert(k, v)
            if created:
                # find the node we just linked (level-0 walk from predecessor)
                node = sl._head.next[0]
                while node is not None and node.key != k:
                    node = node.next[0]
                live_refs.add(node)
            return created

        tracemalloc.start()
        base_snap = None
        for i in range(CYCLES):
            k = i % WINDOW
            tracking_insert(k, k)
            sl.delete(k)
            if i == WINDOW * 2:  # steady state reached; take baseline
                gc.collect()
                base_snap = tracemalloc.take_snapshot()
        gc.collect()
        end_snap = tracemalloc.take_snapshot()

        after_nodes = sum(1 for o in gc.get_objects() if isinstance(o, _Node))
        weak_alive = len(live_refs)

        print(f"\n[reclaim] cycles={CYCLES}")
        print(f"[reclaim] _Node objects: before={before_nodes} after={after_nodes}")
        print(f"[reclaim] weakly-tracked nodes still alive: {weak_alive}")
        print(f"[reclaim] list size at end: {len(sl)}")

        if base_snap is not None:
            diff = sum(s.size_diff for s in end_snap.compare_to(base_snap, "filename"))
            print(f"[reclaim] tracemalloc growth since steady state: {diff} bytes")
            self.assertLess(diff, 1_000_000, "memory keeps growing across cycles")

        # logical size is bounded by the window
        self.assertLessEqual(len(sl), WINDOW)
        # no unbounded accumulation of node objects: everything deleted was
        # reclaimed (head node + whatever is currently linked)
        self.assertLessEqual(after_nodes - before_nodes, len(sl) + 1)
        self.assertLessEqual(weak_alive, len(sl) + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
