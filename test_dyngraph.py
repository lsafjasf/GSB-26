"""dyngraph 自测：随机对拍（增量 vs 全量重算）、边界情形、批次语义、内存有界性。

运行：python3 test_dyngraph.py [-v]
"""

import random
import tracemalloc
import unittest

from dyngraph import DynamicGraph


def incr_partition(g):
    """增量维护的分量划分（规范化为排序后的列表）。"""
    comps = {}
    for v, cid in g.comp.items():
        comps.setdefault(cid, set()).add(v)
    return sorted(sorted(s) for s in comps.values())


def full_partition(g):
    """全量重算的分量划分。"""
    labels = g.recompute_components()
    comps = {}
    for v, lab in labels.items():
        comps.setdefault(lab, set()).add(v)
    return sorted(sorted(s) for s in comps.values())


def check_consistent(t, g):
    t.assertEqual(incr_partition(g), full_partition(g))
    # 抽查 pairwise 查询与全量标签一致
    labels = g.recompute_components()
    vs = list(labels)
    for _ in range(min(300, len(vs) * 2)):
        a = random.choice(vs) if vs else 0
        b = random.choice(vs) if vs else 0
        t.assertEqual(g.connected(a, b), labels.get(a) == labels.get(b) and a in labels)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_graph(self):
        g = DynamicGraph()
        self.assertEqual(g.component_count(), 0)
        self.assertFalse(g.connected(0, 1))
        self.assertFalse(g.connected(0, 0))
        self.assertFalse(g.remove_edge(0, 1))  # 空图上删边：no-op

    def test_single_vertex(self):
        g = DynamicGraph()
        g.add_vertex(7)
        self.assertTrue(g.connected(7, 7))
        self.assertFalse(g.connected(7, 8))
        self.assertEqual(g.component_count(), 1)
        check_consistent(self, g)

    def test_self_loop(self):
        g = DynamicGraph()
        g.add_edge(1, 1)
        g.add_edge(1, 1)  # 自环重复
        self.assertEqual(g.component_count(), 1)
        self.assertTrue(g.connected(1, 1))
        self.assertFalse(g.connected(1, 2))
        self.assertTrue(g.remove_edge(1, 1))
        self.assertTrue(g.remove_edge(1, 1))
        self.assertFalse(g.remove_edge(1, 1))  # 已删光
        check_consistent(self, g)

    def test_parallel_edges(self):
        g = DynamicGraph()
        g.add_edge(0, 1)
        g.add_edge(0, 1)  # 重复边：第二份拷贝
        g.remove_edge(0, 1)
        self.assertTrue(g.connected(0, 1))  # 还剩一份拷贝
        g.remove_edge(0, 1)
        self.assertFalse(g.connected(0, 1))
        self.assertFalse(g.remove_edge(0, 1))  # 删除不存在的边
        check_consistent(self, g)

    def test_delete_nonexistent_keeps_state(self):
        g = DynamicGraph()
        g.add_edge(0, 1)
        before = incr_partition(g)
        self.assertFalse(g.remove_edge(2, 3))
        self.assertFalse(g.remove_edge(0, 2))
        self.assertEqual(incr_partition(g), before)

    def test_split_on_bridge_delete(self):
        g = DynamicGraph()
        for i in range(9):
            g.add_edge(i, i + 1)  # 链 0-1-...-9
        self.assertEqual(g.component_count(), 1)
        g.remove_edge(4, 5)  # 删桥：分量分裂
        self.assertEqual(g.component_count(), 2)
        self.assertTrue(g.connected(0, 4))
        self.assertTrue(g.connected(5, 9))
        self.assertFalse(g.connected(0, 9))
        check_consistent(self, g)

    def test_replacement_edge_no_split(self):
        g = DynamicGraph()
        for i in range(9):
            g.add_edge(i, i + 1)
        g.add_edge(0, 9)  # 成环：任意树边都有替换边
        g.remove_edge(4, 5)
        self.assertEqual(g.component_count(), 1)
        self.assertTrue(g.connected(0, 9))
        check_consistent(self, g)


class DifferentialTest(unittest.TestCase):
    """随机增删序列对拍：每步之后增量结果必须与全量重算一致。"""

    def run_sequence(self, n_vertices, n_ops, seed, batch=False):
        random.seed(seed)
        g = DynamicGraph()
        live = []
        ops_log = []
        for _ in range(n_ops):
            r = random.random()
            if r < 0.02:
                op = ("add", random.randrange(n_vertices),) * 1
                op = ("add", random.randrange(n_vertices), random.randrange(n_vertices))
                op = ("add", op[1], op[1])  # 自环
            elif r < 0.55 or not live:
                op = ("add", random.randrange(n_vertices), random.randrange(n_vertices))
                if op[1] != op[2]:
                    live.append(op[1:])
            elif r < 0.95:
                i = random.randrange(len(live))
                u, v = live.pop(i)
                op = ("del", u, v)
                if random.random() < 0.3:
                    live.append((u, v))  # 可能仍有拷贝，稍后再删
            else:
                op = ("del", random.randrange(n_vertices), random.randrange(n_vertices))  # 可能不存在
            ops_log.append(op)
            if batch:
                g.apply_batch([op])
            elif op[0] == "add":
                g.add_edge(op[1], op[2])
            else:
                g.remove_edge(op[1], op[2])
            check_consistent(self, g)
        return g, ops_log

    def test_random_ops_small(self):
        for seed in range(5):
            self.run_sequence(n_vertices=40, n_ops=800, seed=seed)

    def test_random_ops_dense_splits(self):
        # 顶点多、边少：删除更容易造成分裂
        for seed in range(3):
            self.run_sequence(n_vertices=120, n_ops=600, seed=100 + seed)

    def test_random_batches(self):
        random.seed(7)
        g1, g2 = DynamicGraph(), DynamicGraph()
        live = []
        for _ in range(60):
            batch = []
            for _ in range(random.randrange(1, 20)):
                if live and random.random() < 0.45:
                    i = random.randrange(len(live))
                    u, v = live.pop(i)
                    batch.append(("del", u, v))
                else:
                    u, v = random.randrange(60), random.randrange(60)
                    batch.append(("add", u, v))
                    if u != v:
                        live.append((u, v))
            g1.apply_batch(batch)
            for op in batch:  # g2 逐条应用，殊途同归
                (g2.add_edge if op[0] == "add" else g2.remove_edge)(op[1], op[2])
            check_consistent(self, g1)
            self.assertEqual(incr_partition(g1), incr_partition(g2))


class BatchOrderTest(unittest.TestCase):
    def test_batch_internal_order_irrelevant(self):
        random.seed(42)
        base = DynamicGraph()
        history = []
        for _ in range(300):
            u, v = random.randrange(50), random.randrange(50)
            base.add_edge(u, v)
            history.append((u, v))
        for trial in range(10):
            batch = []
            for _ in range(30):
                if random.random() < 0.5:
                    batch.append(("add", random.randrange(50), random.randrange(50)))
                else:
                    batch.append(("del", random.randrange(50), random.randrange(50)))
            results = []
            for _ in range(3):
                g = DynamicGraph()
                for u, v in history:
                    g.add_edge(u, v)
                shuffled = batch[:]
                random.shuffle(shuffled)
                g.apply_batch(shuffled)
                results.append((incr_partition(g), g._stats()["adj_entries"]))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[1], results[2])
            check_consistent(self, g)


class MemoryTest(unittest.TestCase):
    def test_memory_bounded_by_live_graph(self):
        random.seed(1)
        n = 300
        g = DynamicGraph()
        for i in range(n):  # 固定顶点集
            g.add_vertex(i)
        baseline = g._stats()
        snapshots = []
        for rnd in range(20):
            edges = set()
            while len(edges) < 400:
                u, v = random.randrange(n), random.randrange(n)
                if u != v:
                    edges.add((min(u, v), max(u, v)))
            for u, v in edges:
                g.add_edge(u, v)
            for u, v in edges:
                g.remove_edge(u, v)
            snapshots.append(g._stats())
        # 每轮增删完全对冲后，内部结构规模必须回到基线（不随轮次增长）
        for s in snapshots:
            self.assertEqual(s, baseline)

    def test_tracemalloc_stable_across_rounds(self):
        random.seed(2)
        n = 200
        g = DynamicGraph()
        for i in range(n):
            g.add_vertex(i)

        def one_round():
            edges = set()
            while len(edges) < 300:
                u, v = random.randrange(n), random.randrange(n)
                if u != v:
                    edges.add((min(u, v), max(u, v)))
            for u, v in edges:
                g.add_edge(u, v)
            for u, v in edges:
                g.remove_edge(u, v)

        for _ in range(3):  # 预热
            one_round()
        tracemalloc.start()
        for _ in range(5):
            one_round()
        mid = tracemalloc.get_traced_memory()[0]
        for _ in range(15):
            one_round()
        last = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        # 15 轮的累计增量必须基本为零：斜率有界即不随增删次数无限增长
        self.assertLessEqual(last - mid, 8192,
                             f"内存随轮次增长: {last - mid} bytes / 15 rounds")


if __name__ == "__main__":
    unittest.main()
