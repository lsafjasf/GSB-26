"""WeightedFairScheduler 单元测试（仅标准库 unittest）。"""

import unittest

from fair_scheduler import (
    QueueExistsError,
    NoSuchQueueError,
    WeightedFairScheduler,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


def make_scheduler(weights, clock=None):
    sched = WeightedFairScheduler(clock=clock) if clock else WeightedFairScheduler()
    for name, w in weights.items():
        sched.add_queue(name, w)
    return sched


def drain(sched, limit=10_000_000):
    out = []
    for _ in range(limit):
        item = sched.dequeue()
        if item is None:
            break
        out.append(item)
    return out


class TestBasicSemantics(unittest.TestCase):
    def test_fifo_within_queue(self):
        sched = make_scheduler({"a": 1})
        for i in range(10):
            sched.enqueue("a", i)
        self.assertEqual(drain(sched), list(range(10)))

    def test_single_queue_always_served(self):
        sched = make_scheduler({"only": 7})
        for i in range(100):
            sched.enqueue("only", f"t{i}")
        out = drain(sched)
        self.assertEqual(len(out), 100)
        self.assertEqual(sched.total_served, 100)

    def test_all_empty_returns_none(self):
        sched = make_scheduler({"a": 1, "b": 2, "c": 3})
        self.assertIsNone(sched.dequeue())
        self.assertEqual(sched.total_served, 0)

    def test_duplicate_queue_rejected(self):
        sched = make_scheduler({"a": 1})
        with self.assertRaises(QueueExistsError):
            sched.add_queue("a", 2)

    def test_unknown_queue_rejected(self):
        sched = make_scheduler({"a": 1})
        with self.assertRaises(NoSuchQueueError):
            sched.enqueue("nope", 1)

    def test_negative_weight_rejected(self):
        sched = WeightedFairScheduler()
        with self.assertRaises(ValueError):
            sched.add_queue("bad", -1)

    def test_set_weight_reconfigures(self):
        sched = make_scheduler({"a": 1, "b": 1})
        sched.set_weight("a", 3)
        for i in range(400):
            sched.enqueue("a", ("a", i))
            sched.enqueue("b", ("b", i))
        # 只取 400 个（100 轮），期间两队列均保持非空
        out = [sched.dequeue() for _ in range(400)]
        ca = sum(1 for x in out if x[0] == "a")
        cb = sum(1 for x in out if x[0] == "b")
        self.assertEqual((ca, cb), (300, 100))


class TestFairness(unittest.TestCase):
    def test_weighted_proportions_within_bound(self):
        weights = {"a": 5, "b": 3, "c": 2, "d": 1}
        sched = make_scheduler(weights)
        for name in weights:
            for i in range(20000):
                sched.enqueue(name, (name, i))
        # 取 11000 个（1000 整轮），期间所有队列保持非空（backlogged）
        out = [sched.dequeue() for _ in range(11000)]
        total = len(out)
        W = sum(weights.values())
        counts = {n: sum(1 for x in out if x[0] == n) for n in weights}
        for name, w in weights.items():
            expected = total * w / W
            bound = w * (1 - w / W)  # 理论偏差上界
            self.assertLessEqual(
                abs(counts[name] - expected), bound + 1e-9,
                f"{name}: |{counts[name]} - {expected}| > {bound}",
            )

    def test_empty_queue_quota_redistributed(self):
        # C 为空：A、B 应各得 1/2，而不是 1/3
        sched = make_scheduler({"a": 1, "b": 1, "c": 1})
        for i in range(3000):
            sched.enqueue("a", ("a", i))
            sched.enqueue("b", ("b", i))
        out = drain(sched)
        ca = sum(1 for x in out if x[0] == "a")
        cb = sum(1 for x in out if x[0] == "b")
        self.assertEqual(ca, 3000)
        self.assertEqual(cb, 3000)
        # 交错验证：不应出现连续 3 个同来源（1:1 重分配）
        sources = [x[0] for x in out]
        self.assertFalse(any(
            sources[i] == sources[i + 1] == sources[i + 2]
            for i in range(len(sources) - 2)
        ))

    def test_queue_emptying_midstream(self):
        # B 中途耗尽后，其配额自动转给 A
        sched = make_scheduler({"a": 1, "b": 1})
        for i in range(100):
            sched.enqueue("a", ("a", i))
            sched.enqueue("b", ("b", i))
        for i in range(100, 500):
            sched.enqueue("a", ("a", i))
        out = drain(sched)
        self.assertEqual(len(out), 600)
        ca = sum(1 for x in out if x[0] == "a")
        self.assertEqual(ca, 500)

    def test_zero_weight_never_served(self):
        sched = make_scheduler({"z": 0, "a": 1})
        for i in range(50):
            sched.enqueue("z", ("z", i))
            sched.enqueue("a", ("a", i))
        out = drain(sched)
        self.assertEqual(len(out), 50)
        self.assertTrue(all(x[0] == "a" for x in out))
        self.assertEqual(sched.pending, 50)  # z 的任务仍在队列中

    def test_all_zero_weight_returns_none(self):
        sched = make_scheduler({"z1": 0, "z2": 0})
        sched.enqueue("z1", 1)
        sched.enqueue("z2", 2)
        self.assertIsNone(sched.dequeue())

    def test_extreme_weight_disparity(self):
        weights = {"big": 1000, "small": 1}
        sched = make_scheduler(weights)
        for i in range(100000):
            sched.enqueue("big", ("big", i))
        for i in range(100):
            sched.enqueue("small", ("small", i))
        out = drain(sched)
        counts = {"big": 0, "small": 0}
        for x in out:
            counts[x[0]] += 1
        self.assertEqual(counts["small"], 100)   # 小队列不被饿死
        self.assertEqual(counts["big"], 100000)


class TestStarvationBound(unittest.TestCase):
    def test_max_gap_within_bound(self):
        # 任意非空队列 i 的相邻两次服务间隔（其它队列的出队数）
        # 上界为 (W - w_i) * quantum
        weights = {"a": 5, "b": 3, "c": 2, "d": 1}
        W = sum(weights.values())
        sched = make_scheduler(weights)
        for name in weights:
            for i in range(5000):
                sched.enqueue(name, (name, i))
        last_seen = {}
        max_gap = {n: 0 for n in weights}
        for step, item in enumerate(drain(sched)):
            name = item[0]
            if name in last_seen:
                gap = step - last_seen[name] - 1
                max_gap[name] = max(max_gap[name], gap)
            last_seen[name] = step
        for name, w in weights.items():
            bound = W - w
            self.assertLessEqual(max_gap[name], bound,
                                 f"{name}: gap {max_gap[name]} > bound {bound}")


class TestInjectedClock(unittest.TestCase):
    def test_clock_injection(self):
        clock = FakeClock()
        sched = make_scheduler({"a": 1}, clock=clock)
        self.assertEqual(sched.stats()["created_at"], 1000.0)
        sched.enqueue("a", "x")
        clock.advance(2.5)
        sched.dequeue()
        self.assertEqual(sched.stats()["last_served_at"], 1002.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
