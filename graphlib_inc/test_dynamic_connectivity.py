"""Unit tests + randomized differential tests (对拍) for DynamicConnectivity.

Run:  python3 -m unittest graphlib_inc.test_dynamic_connectivity -v
"""

import random
import resource
import sys
import unittest

from graphlib_inc.dynamic_connectivity import DynamicConnectivity


# --------------------------------------------------------------------- #
# reference model: full recompute from the current edge multiset
# --------------------------------------------------------------------- #
class ReferenceModel:
    """Naive multigraph: every query recomputes components from scratch."""

    def __init__(self):
        self.edges = {}   # (a, b) normalized, a != b -> multiplicity
        self.loops = {}   # vertex -> multiplicity
        self.vertices = set()

    @staticmethod
    def _norm(u, v):
        return (u, v) if u <= v else (v, u)

    def add_edge(self, u, v):
        self.vertices.update((u, v))
        if u == v:
            self.loops[u] = self.loops.get(u, 0) + 1
            return
        k = self._norm(u, v)
        self.edges[k] = self.edges.get(k, 0) + 1

    def remove_edge(self, u, v):
        if u == v:
            if self.loops.get(u, 0) == 0:
                return False
            self.loops[u] -= 1
            if self.loops[u] == 0:
                del self.loops[u]
            return True
        k = self._norm(u, v)
        if self.edges.get(k, 0) == 0:
            return False
        self.edges[k] -= 1
        if self.edges[k] == 0:
            del self.edges[k]
        return True

    def components(self):
        parent = {v: v for v in self.vertices}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (a, b) in self.edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        groups = {}
        for v in self.vertices:
            groups.setdefault(find(v), set()).add(v)
        return [frozenset(g) for g in groups.values()]

    def connected(self, u, v):
        if u not in self.vertices or v not in self.vertices:
            return u == v
        return any(u in c and v in c for c in self.components())


def same_partition(pa, pb):
    return set(pa) == set(pb)


# --------------------------------------------------------------------- #
# edge-case unit tests
# --------------------------------------------------------------------- #
class TestEdgeCases(unittest.TestCase):
    def test_empty_graph(self):
        dc = DynamicConnectivity()
        self.assertEqual(dc.num_components, 0)
        self.assertEqual(dc.num_edges, 0)
        self.assertFalse(dc.connected("a", "b"))   # unknown vertices
        self.assertTrue(dc.connected("a", "a"))    # reflexive even if unknown
        self.assertEqual(dc.component_size("a"), 1)
        self.assertFalse(dc.remove_edge("a", "b"))  # delete on empty graph

    def test_single_vertex(self):
        dc = DynamicConnectivity()
        dc.add_vertex("x")
        self.assertEqual(dc.num_components, 1)
        self.assertTrue(dc.connected("x", "x"))
        self.assertFalse(dc.connected("x", "y"))

    def test_self_loop(self):
        dc = DynamicConnectivity()
        dc.add_edge(1, 1)
        dc.add_edge(1, 1)          # duplicate loop
        self.assertEqual(dc.num_edges, 2)
        self.assertEqual(dc.num_components, 1)     # loop changes nothing
        self.assertFalse(dc.connected(1, 2))
        self.assertTrue(dc.remove_edge(1, 1))
        self.assertTrue(dc.remove_edge(1, 1))
        self.assertFalse(dc.remove_edge(1, 1))     # third copy never existed
        self.assertEqual(dc.num_edges, 0)

    def test_parallel_edges(self):
        dc = DynamicConnectivity()
        dc.add_edge(1, 2)
        dc.add_edge(2, 1)          # same unordered pair, second copy
        dc.add_edge(1, 2)          # third copy
        self.assertEqual(dc.num_edges, 3)
        self.assertTrue(dc.connected(1, 2))
        self.assertTrue(dc.remove_edge(1, 2))
        self.assertTrue(dc.remove_edge(2, 1))
        self.assertTrue(dc.connected(1, 2))        # one copy remains
        self.assertTrue(dc.remove_edge(1, 2))
        self.assertFalse(dc.connected(1, 2))       # last copy gone
        self.assertFalse(dc.remove_edge(1, 2))
        self.assertEqual(dc.num_edges, 0)

    def test_delete_nonexistent_edge(self):
        dc = DynamicConnectivity()
        dc.add_edge(1, 2)
        before = dc.components()
        self.assertFalse(dc.remove_edge(2, 3))     # never inserted
        self.assertFalse(dc.remove_edge(3, 4))     # unknown vertices
        self.assertFalse(dc.remove_edge(1, 1))     # no loop present
        self.assertTrue(same_partition(before, dc.components()))
        self.assertEqual(dc.num_edges, 1)

    def test_delete_causes_split(self):
        dc = DynamicConnectivity()
        dc.add_edges([(0, 1), (1, 2), (2, 3)])     # path 0-1-2-3
        self.assertEqual(dc.num_components, 1)
        dc.remove_edge(1, 2)
        self.assertEqual(dc.num_components, 2)
        self.assertTrue(dc.connected(0, 1))
        self.assertTrue(dc.connected(2, 3))
        self.assertFalse(dc.connected(0, 3))
        self.assertEqual(dc.component_size(0), 2)

    def test_cycle_stays_connected(self):
        dc = DynamicConnectivity()
        dc.add_edges([(0, 1), (1, 2), (2, 0)])
        dc.remove_edge(1, 2)                        # cycle -> path
        self.assertTrue(dc.connected(0, 2))
        self.assertEqual(dc.num_components, 1)

    def test_split_and_remerge(self):
        dc = DynamicConnectivity()
        n = 200
        dc.add_edges([(i, i + 1) for i in range(n - 1)])   # long path
        for cut in (50, 100, 150):
            dc.remove_edge(cut, cut + 1)
        self.assertEqual(dc.num_components, 4)
        dc.add_edge(100, 101)
        self.assertEqual(dc.num_components, 3)
        self.assertTrue(dc.connected(99, 102))
        self.assertFalse(dc.connected(0, 199))

    def test_batch_settlement(self):
        dc = DynamicConnectivity()
        dc.add_edges([(0, 1), (1, 2), (2, 3), (3, 4)])
        dc.apply_batch(adds=[(0, 4), (10, 11)], dels=[(1, 2), (2, 3)])
        self.assertTrue(dc.connected(0, 4))        # 0-1 ... 3-4 plus 0-4
        self.assertTrue(dc.connected(1, 4))
        self.assertFalse(dc.connected(0, 10))
        self.assertTrue(dc.connected(10, 11))
        self.assertFalse(dc.connected(0, 2))       # vertex 2 is isolated now
        self.assertEqual(dc.num_components, 3)     # {0,1,3,4}, {2}, {10,11}


# --------------------------------------------------------------------- #
# randomized differential test: incremental vs full recompute
# --------------------------------------------------------------------- #
class TestDifferential(unittest.TestCase):
    def _run_sequence(self, seed, n_vertices, n_ops, batch_prob=0.15):
        rng = random.Random(seed)
        dc = DynamicConnectivity()
        ref = ReferenceModel()
        live = []  # edges currently present (with multiplicity)

        def rand_edge():
            a, b = rng.randrange(n_vertices), rng.randrange(n_vertices)
            return (a, b) if a <= b else (b, a)  # normalized: undirected edge

        for step in range(n_ops):
            r = rng.random()
            if r < batch_prob:
                # settle a whole batch (adds and dels on disjoint pairs)
                adds = [rand_edge() for _ in range(rng.randrange(1, 6))]
                pool = [e for e in live if e[0] != e[1]]
                rng.shuffle(pool)
                dels = pool[: rng.randrange(0, 4)]
                banned = {frozenset(e) for e in dels}
                adds = [e for e in adds if frozenset(e) not in banned]
                dc.apply_batch(adds=adds, dels=dels)
                for e in dels:
                    ref.remove_edge(*e)
                    live.remove(e)
                for e in adds:
                    ref.add_edge(*e)
                    live.append(e)
            elif r < 0.55 or not live:
                e = rand_edge()
                dc.add_edge(*e)
                ref.add_edge(*e)
                live.append(e)
            elif r < 0.90:
                e = live.pop(rng.randrange(len(live)))
                self.assertTrue(dc.remove_edge(*e))
                self.assertTrue(ref.remove_edge(*e))
            else:
                # delete of a (probably) non-existent edge
                e = rand_edge()
                got_dc = dc.remove_edge(*e)
                got_ref = ref.remove_edge(*e)
                self.assertEqual(got_dc, got_ref)
                if got_ref:
                    live.remove(e)

            # spot-check reachability on random pairs
            for _ in range(6):
                a = rng.randrange(n_vertices)
                b = rng.randrange(n_vertices)
                self.assertEqual(
                    dc.connected(a, b), ref.connected(a, b),
                    f"seed={seed} step={step} connected({a},{b})",
                )
            self.assertEqual(dc.num_components, len(ref.components()))

            # full partition comparison every 40 steps and at the end
            if step % 40 == 0 or step == n_ops - 1:
                self.assertTrue(
                    same_partition(dc.components(), ref.components()),
                    f"seed={seed} step={step}: partition mismatch",
                )

    def test_random_sequences_small(self):
        for seed in range(30):
            with self.subTest(seed=seed):
                self._run_sequence(seed, n_vertices=12, n_ops=400)

    def test_random_sequences_medium(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self._run_sequence(1000 + seed, n_vertices=120, n_ops=1500)

    def test_delete_heavy_sequences(self):
        # build dense then mostly delete: forces many component splits
        for seed in range(5):
            with self.subTest(seed=seed):
                rng = random.Random(5000 + seed)
                dc = DynamicConnectivity()
                ref = ReferenceModel()
                n = 60
                edges = [(i, j) for i in range(n) for j in range(i + 1, n)
                         if rng.random() < 0.15]
                for e in edges:
                    dc.add_edge(*e)
                    ref.add_edge(*e)
                rng.shuffle(edges)
                for k, e in enumerate(edges):
                    self.assertTrue(dc.remove_edge(*e))
                    ref.remove_edge(*e)
                    if k % 25 == 0:
                        self.assertTrue(
                            same_partition(dc.components(), ref.components()))
                        self.assertEqual(dc.num_components,
                                         len(ref.components()))
                self.assertTrue(same_partition(dc.components(),
                                               ref.components()))

    def test_batch_order_independence(self):
        # any interleaving of the same batch must give the same partition
        for seed in range(10):
            rng = random.Random(7000 + seed)
            n = 40
            base_edges = [(rng.randrange(n), rng.randrange(n))
                          for _ in range(120)]
            dels = random.Random(seed + 1).sample(base_edges, 30)
            adds = [(rng.randrange(n), rng.randrange(n)) for _ in range(30)]
            banned = {frozenset(e) for e in dels}
            adds = [e for e in adds if frozenset(e) not in banned]

            partitions = []
            for trial in range(4):
                dc = DynamicConnectivity()
                dc.add_edges(base_edges)
                ops = ([("add", e) for e in adds]
                       + [("del", e) for e in dels])
                random.Random(seed + 100 + trial).shuffle(ops)
                for kind, e in ops:
                    if kind == "add":
                        dc.add_edge(*e)
                    else:
                        dc.remove_edge(*e)
                partitions.append(set(dc.components()))
            for p in partitions[1:]:
                self.assertEqual(partitions[0], p)


# --------------------------------------------------------------------- #
# memory stability: RSS must not grow with the number of updates
# --------------------------------------------------------------------- #
class TestMemoryStability(unittest.TestCase):
    @staticmethod
    def _rss_kb():
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    @unittest.skipUnless(sys.platform.startswith("linux"), "linux only")
    def test_memory_does_not_grow_with_updates(self):
        rng = random.Random(42)
        n = 60_000
        dc = DynamicConnectivity()
        dc.add_edges((rng.randrange(n), rng.randrange(n))
                     for _ in range(120_000))

        def rss():
            with open("/proc/self/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS"):
                        return int(line.split()[1])
            return 0

        rounds, per_round = 30, 20_000
        samples = []
        live = []
        for rnd in range(rounds):
            adds = [(rng.randrange(n), rng.randrange(n))
                    for _ in range(per_round)]
            dc.add_edges(adds)
            live.extend(adds)
            # remove exactly as many edges as added: the live-edge count
            # stays constant, so any RSS growth would indicate a leak
            pick = [live.pop(rng.randrange(len(live)))
                    for _ in range(per_round)]
            dc.remove_edges(pick)
            samples.append(rss())
        # 600k updates total.  The first rounds let the Python allocator
        # reach its steady-state arena size; afterwards RSS must be flat.
        # A real leak would grow linearly without bound instead.
        tail = samples[-6:]
        self.assertLess(tail[-1] - tail[0], 512,  # < 0.5 MB over 100k updates
                        f"RSS keeps growing in steady state: {samples}")


if __name__ == "__main__":
    unittest.main()
