"""再平衡库自测：规划最优性、并发一致性对拍、崩溃恢复、边界情形。

运行：python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rebalance import (Cluster, CrashInjector, Migrator, Node, SimulatedCrash,
                       StaleEpochError, balanced_targets, compute_plan,
                       estimator, initial_assignment, moves_lower_bound,
                       recover_cluster)


class TestPlanner(unittest.TestCase):
    def test_moves_equal_lower_bound(self):
        cases = [
            (64, ["a", "b", "c", "d", "e"], ["a", "b", "c", "d", "e", "f", "g"]),
            (64, ["a", "b", "c"], ["a", "b"]),
            (64, ["a", "b", "c"], ["x", "y", "z"]),      # 全部替换
            (1, ["a"], ["a", "b"]),                       # 分区数为 1
            (3, ["a", "b"], ["a", "b", "c", "d", "e"]),   # 节点数 > 分区数
            (10, ["a"], ["a"]),                           # 单节点
            (10, ["a"], ["a", "b", "c", "d"]),
        ]
        for P, old, new in cases:
            plan = compute_plan(P, old, new)
            self.assertEqual(plan.num_moves, plan.lower_bound, (P, old, new))
            self.assertEqual(plan.num_moves,
                             moves_lower_bound(plan.old_assignment, new))
            # 新归属必须均衡（每节点分区数 == 目标数）
            from collections import Counter
            cnt = Counter(plan.new_assignment)
            self.assertEqual(cnt, Counter(balanced_targets(P, new)))

    def test_full_replacement_moves_all(self):
        plan = compute_plan(16, ["a", "b"], ["c", "d"])
        self.assertEqual(plan.num_moves, 16)

    def test_single_node_no_moves(self):
        plan = compute_plan(10, ["a"], ["a"])
        self.assertEqual(plan.num_moves, 0)

    def test_one_partition(self):
        plan = compute_plan(1, ["a"], ["b"])
        self.assertEqual(plan.num_moves, 1)
        self.assertEqual(plan.moves[0].partition, 0)

    def test_more_nodes_than_partitions(self):
        plan = compute_plan(3, ["a"], ["a", "b", "c", "d", "e"])
        self.assertEqual(plan.num_moves, 2)  # 3 个分区分给 3 个节点
        self.assertEqual(len(set(plan.new_assignment)), 3)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            compute_plan(0, ["a"], ["a"])
        with self.assertRaises(ValueError):
            compute_plan(4, ["a"], [])
        with self.assertRaises(ValueError):
            compute_plan(4, ["a"], ["a", "a"])


class MigrationBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.journal_path = os.path.join(self.dir, "journal.json")

    def build(self, P, old_nodes, keys_per_partition=5):
        c = Cluster(old_nodes, P)
        for pid in range(P):
            for k in range(keys_per_partition):
                c.write(pid, f"k{pid}-{k}", f"v{pid}-{k}")
        return c

    def assert_data_intact(self, c, P, keys_per_partition=5):
        for pid in range(P):
            for k in range(keys_per_partition):
                self.assertEqual(c.read(pid, f"k{pid}-{k}"), f"v{pid}-{k}",
                                 f"pid={pid} k={k}")


class TestMigration(MigrationBase):
    def test_basic_migration_and_progress(self):
        P = 16
        c = self.build(P, ["a", "b", "c"])
        plan = compute_plan(P, ["a", "b", "c"], ["b", "c", "d", "e"])
        m = Migrator.start(c, plan, self.journal_path)
        self.assertEqual(m.progress()["done"], 0)
        m.run()
        prog = m.progress()
        self.assertTrue(prog["complete"])
        self.assertEqual(prog["done"], plan.num_moves)
        self.assertEqual(c.table, plan.new_assignment)
        self.assert_data_intact(c, P)
        # 源副本已清理：每个分区全集群只有一份
        for pid in range(P):
            holders = [n.id for n in c.nodes.values() if pid in n.data]
            self.assertEqual(holders, [c.table[pid]])

    def test_fencing_rejects_stale_epoch(self):
        # 单元级：同一节点上过期 epoch 的写被拒绝
        node = Node("n1")
        node.write(0, "k", "v1", epoch=5)
        with self.assertRaises(StaleEpochError):
            node.write(0, "k", "v2", epoch=3)
        self.assertEqual(node.read(0, "k"), "v1")
        self.assertEqual(node.rejected_stale_writes, 1)

        # 迁移后：新主只接受切换后的新 epoch，旧 epoch 的迟到写被 fencing
        c = self.build(4, ["a", "b"])
        plan = compute_plan(4, ["a", "b"], ["b", "c"])
        Migrator.start(c, plan, self.journal_path).run()
        moved = plan.moves[0]
        new_owner = c.nodes[moved.dst]
        with self.assertRaises(StaleEpochError):
            new_owner.write(moved.partition, "x", "y", epoch=1)  # 切换前 epoch=1

    def test_single_writer_during_migration(self):
        """并发读写 + 迁移：不丢写、读单调、任意时刻单主。"""
        P = 16
        c = self.build(P, ["a", "b", "c"], keys_per_partition=0)
        plan = compute_plan(P, ["a", "b", "c"], ["b", "c", "d", "e"])
        m = Migrator.start(c, plan, self.journal_path, chunk_keys=4)

        stop = threading.Event()
        counters = [{} for _ in range(P)]  # 对拍模型：key -> 已确认写次数
        model_lock = threading.Lock()
        errors = []

        def writer(wid):
            i = 0
            while not stop.is_set():
                pid = (wid * 7 + i) % P
                key = f"w{wid}-{i % 50}"
                try:
                    c.increment(pid, key)
                    with model_lock:
                        counters[pid][key] = counters[pid].get(key, 0) + 1
                except Exception as e:  # noqa
                    errors.append(e)
                i += 1

        def reader():
            last = {}
            while not stop.is_set():
                for pid in range(P):
                    for key in (f"w{w}-{k}" for w in range(3) for k in range(50)):
                        v = c.read(pid, key)
                        if v is None:
                            continue
                        prev = last.get((pid, key), 0)
                        if v < prev:
                            errors.append(
                                AssertionError(f"read went backwards: {v} < {prev}"))
                        last[(pid, key)] = v

        threads = ([threading.Thread(target=writer, args=(w,)) for w in range(3)]
                   + [threading.Thread(target=reader) for _ in range(2)])
        for t in threads:
            t.start()
        m.run()  # 迁移与读写并发进行
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # 对拍：每个 key 的最终值 == 模型中已确认写次数（不丢写）
        for pid in range(P):
            for key, expect in counters[pid].items():
                self.assertEqual(c.read(pid, key), expect,
                                 f"lost write pid={pid} key={key}")
        # 单主：每个分区归属日志中 owner 变更次数 == 迁移次数，无回退
        for pid in range(P):
            owners = [o for o, _ in c.ownership_log[pid]]
            self.assertEqual(len(owners), len(set(owners)) or 1)
            self.assertEqual(owners[-1], c.table[pid])

    def test_crash_recovery_at_every_checkpoint(self):
        """在每个 checkpoint 强杀后重启：状态自洽，不重迁、不漏迁、不丢数据。"""
        P = 8
        old, new = ["a", "b", "c"], ["b", "c", "d"]
        plan = compute_plan(P, old, new)
        num_checkpoints = 2 * plan.num_moves  # 每个 move 有 copying/done 两个点

        for crash_at in range(num_checkpoints + 1):
            c = self.build(P, old)
            injector = CrashInjector(crash_at)
            m = Migrator.start(c, plan, self.journal_path,
                               checkpoint_hook=injector)
            try:
                m.run()
            except SimulatedCrash:
                pass
            # 重启：节点数据还在（独立进程），路由/迁移器从 journal 恢复
            c2, m2 = recover_cluster(self.journal_path, c.nodes)
            m2.run()
            self.assertEqual(c2.table, plan.new_assignment,
                             f"crash_at={crash_at}")
            self.assert_data_intact(c2, P)
            self.assertTrue(m2.progress()["complete"])
            # 每个分区恰好一个持有者
            for pid in range(P):
                holders = [n.id for n in c2.nodes.values() if pid in n.data]
                self.assertEqual(holders, [c2.table[pid]],
                                 f"crash_at={crash_at} pid={pid}")

    def test_recovery_does_not_remigrate_done_partitions(self):
        P = 8
        old, new = ["a", "b"], ["b", "c"]
        plan = compute_plan(P, old, new)
        c = self.build(P, old)
        # 在恰好一半 move 完成后崩溃
        injector = CrashInjector(2 * (plan.num_moves // 2))
        m = Migrator.start(c, plan, self.journal_path, checkpoint_hook=injector)
        with self.assertRaises(SimulatedCrash):
            m.run()
        done_before = sum(1 for mv in m.journal.state["moves"]
                          if mv["state"] == "done")
        self.assertGreater(done_before, 0)
        c2, m2 = recover_cluster(self.journal_path, c.nodes)
        m2.run()
        # 崩溃前已完成的分区，恢复后零次拷贝
        done_pids = {mv["partition"] for mv in m.journal.state["moves"]
                     if mv["state"] == "done"}
        for pid in done_pids:
            self.assertNotIn(pid, m2.stats["copies_per_partition"])


class TestEdgeCases(MigrationBase):
    def run_e2e(self, P, old, new):
        c = self.build(P, old)
        plan = compute_plan(P, old, new)
        m = Migrator.start(c, plan, self.journal_path)
        m.run()
        self.assertEqual(c.table, plan.new_assignment)
        self.assert_data_intact(c, P)
        return plan

    def test_single_node(self):
        plan = self.run_e2e(8, ["a"], ["a"])
        self.assertEqual(plan.num_moves, 0)

    def test_single_node_scale_out(self):
        self.run_e2e(8, ["a"], ["a", "b", "c"])

    def test_full_replacement(self):
        plan = self.run_e2e(8, ["a", "b"], ["x", "y"])
        self.assertEqual(plan.num_moves, 8)

    def test_one_partition(self):
        self.run_e2e(1, ["a"], ["b"])

    def test_more_nodes_than_partitions(self):
        self.run_e2e(2, ["a"], ["a", "b", "c", "d"])

    def test_shrink_to_one(self):
        self.run_e2e(8, ["a", "b", "c", "d"], ["a"])


class TestEstimator(unittest.TestCase):
    def test_move_count_matches_planner(self):
        P = 32
        old = ["a", "b", "c"]
        new = ["a", "b", "c", "d", "e"]
        plan = compute_plan(P, old, new)
        self.assertEqual(
            estimator.estimate_move_count(P, plan.old_assignment, new),
            plan.num_moves)

    def test_time_formula(self):
        # D=1000B, rate=100B/s, 10 moves, cutover=0.1s -> 10s + 1s
        t = estimator.estimate_time_seconds(1000, 100, 10, 0.1)
        self.assertAlmostEqual(t, 11.0)


if __name__ == "__main__":
    unittest.main()
