"""Self-tests for fair_scheduler (stdlib unittest only)."""

import math
import unittest

from fair_scheduler import FairScheduler


def run_dequeues(sched, n):
    """Dequeue up to n tasks; return list of queue names in serve order."""
    order = []
    for _ in range(n):
        item = sched.dequeue()
        if item is None:
            break
        order.append(item[0])
    return order


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class TestBasics(unittest.TestCase):
    def test_fifo_within_queue(self):
        s = FairScheduler()
        s.add_queue("a")
        for i in range(5):
            s.enqueue("a", i)
        got = [s.dequeue()[1] for _ in range(5)]
        self.assertEqual(got, [0, 1, 2, 3, 4])

    def test_single_queue_gets_everything(self):
        s = FairScheduler()
        s.add_queue("only", weight=7)
        for i in range(100):
            s.enqueue("only", i)
        order = run_dequeues(s, 100)
        self.assertEqual(order, ["only"] * 100)
        self.assertIsNone(s.dequeue())

    def test_all_empty_returns_none(self):
        s = FairScheduler()
        s.add_queue("a", weight=1)
        s.add_queue("b", weight=2)
        self.assertIsNone(s.dequeue())  # nothing ever enqueued
        s.enqueue("a", "x")
        self.assertEqual(s.dequeue(), ("a", "x"))
        self.assertIsNone(s.dequeue())  # drained again

    def test_unknown_queue_raises(self):
        s = FairScheduler()
        s.add_queue("a")
        with self.assertRaises(KeyError):
            s.enqueue("nope", 1)
        with self.assertRaises(KeyError):
            s.set_weight("nope", 2)

    def test_negative_weight_rejected(self):
        s = FairScheduler()
        with self.assertRaises(ValueError):
            s.add_queue("bad", weight=-1)
        s.add_queue("ok")
        with self.assertRaises(ValueError):
            s.set_weight("ok", -0.5)


class TestFairness(unittest.TestCase):
    def test_weighted_proportions(self):
        weights = {"a": 1, "b": 2, "c": 3}
        s = FairScheduler()
        for name, w in weights.items():
            s.add_queue(name, weight=w)
        total = 6000
        for name in weights:
            for i in range(total):  # keep every queue backlogged
                s.enqueue(name, i)
        order = run_dequeues(s, total)
        counts = {name: order.count(name) for name in weights}
        wsum = sum(weights.values())
        for name, w in weights.items():
            ideal = total * w / wsum
            # Per-run deviation is bounded by a small constant (see (F)),
            # far below the 1% tolerance asserted here.
            self.assertAlmostEqual(counts[name], ideal, delta=ideal * 0.01 + 2)

    def test_normalized_lag_bound_holds_continuously(self):
        # Bound (F): |c_i/w_i - c_j/w_j| <= 1/w_i + 1/w_j at ALL times,
        # not just at the end of the run.
        weights = {"a": 1, "b": 2, "c": 3, "d": 4}
        s = FairScheduler()
        for name, w in weights.items():
            s.add_queue(name, weight=w)
            for i in range(3000):
                s.enqueue(name, i)
        counts = {name: 0 for name in weights}
        max_lag = 0.0
        for _ in range(3000):
            name, _ = s.dequeue()
            counts[name] += 1
            names = list(weights)
            for x in range(len(names)):
                for y in range(x + 1, len(names)):
                    i, j = names[x], names[y]
                    lag = abs(counts[i] / weights[i] - counts[j] / weights[j])
                    max_lag = max(max_lag, lag)
        bound = max(
            1.0 / weights[i] + 1.0 / weights[j]
            for i in weights
            for j in weights
            if i != j
        )
        self.assertLessEqual(max_lag, bound + 1e-9)

    def test_extreme_weight_disparity(self):
        # Ratio 1 : 1000, both queues backlogged: counts track the ratio
        # within the constant lag bound (F): |c_t/1 - c_h/1000| <= 1.001.
        s = FairScheduler()
        s.add_queue("tiny", weight=1)
        s.add_queue("huge", weight=1000)
        for i in range(200):
            s.enqueue("tiny", i)
        for i in range(200_000):
            s.enqueue("huge", i)
        order = run_dequeues(s, 101_000)
        ct, ch = order.count("tiny"), order.count("huge")
        self.assertLessEqual(abs(ct / 1 - ch / 1000), 1.001 + 1e-9)
        self.assertLessEqual(abs(ct - 100), 2)

    def test_weight_ratio_one_million(self):
        # Weights 1 vs 1e6: the tiny queue's single task costs it 1 unit
        # of virtual finish time, so the huge queue is served 1e6 times
        # first; tiny is still served exactly once, never starved.
        s = FairScheduler()
        s.add_queue("tiny", weight=1)
        s.add_queue("huge", weight=1_000_000)
        for i in range(1_000_000):
            s.enqueue("huge", i)
        s.enqueue("tiny", "x")
        order = run_dequeues(s, 1_000_001)
        self.assertEqual(order.count("tiny"), 1)
        self.assertEqual(order.count("huge"), 1_000_000)


class TestEmptyQueueRedistribution(unittest.TestCase):
    def test_empty_queue_consumes_no_quota(self):
        s = FairScheduler()
        s.add_queue("a", weight=1)
        s.add_queue("b", weight=1)
        for i in range(10):
            s.enqueue("a", i)
        for i in range(1000):
            s.enqueue("b", i)
        order = run_dequeues(s, 1010)
        self.assertEqual(len(order), 1010)  # never idles while work exists
        self.assertEqual(order.count("a"), 10)
        self.assertEqual(order.count("b"), 1000)
        # While both were backlogged they alternated ~1:1; after "a"
        # drained, "b" must get 100% of the remaining dequeues.
        last_a = max(i for i, n in enumerate(order) if n == "a")
        self.assertTrue(all(n == "b" for n in order[last_a + 1 :]))

    def test_share_scales_when_queue_drains(self):
        # weights 1:2:3; c has few tasks. After c drains, a:b must be 1:2.
        s = FairScheduler()
        s.add_queue("a", weight=1)
        s.add_queue("b", weight=2)
        s.add_queue("c", weight=3)
        for i in range(4000):
            s.enqueue("a", i)
            s.enqueue("b", i)
        for i in range(60):
            s.enqueue("c", i)
        # Run only 2000 dequeues so a and b stay backlogged throughout;
        # c (60 tasks at weight 3/6) drains after ~120 dequeues.
        order = run_dequeues(s, 2000)
        last_c = max(i for i, n in enumerate(order) if n == "c")
        tail = order[last_c + 1 :]
        ca, cb = tail.count("a"), tail.count("b")
        self.assertAlmostEqual(ca / cb, 0.5, delta=0.02)


class TestStarvationFreedom(unittest.TestCase):
    def test_served_within_bound(self):
        # Three heavy always-backlogged queues (w=1) and one tiny queue
        # (w=0.01). Bound (S) for tiny: 1 + 3 * ceil(2 * 1 / 0.01) = 601.
        s = FairScheduler()
        for name in ("h1", "h2", "h3"):
            s.add_queue(name, weight=1)
        s.add_queue("tiny", weight=0.01)
        bound = s.starvation_bound("tiny")
        self.assertEqual(bound, 1 + 3 * math.ceil(2 * 1 / 0.01))
        for trial in range(50):
            for name in ("h1", "h2", "h3"):
                for i in range(bound + 10):
                    s.enqueue(name, (trial, i))
            s.enqueue("tiny", ("task", trial))
            waits = 0
            while True:
                name, task = s.dequeue()
                waits += 1
                if name == "tiny":
                    break
                self.assertLessEqual(waits, bound)
            self.assertEqual(task, ("task", trial))
            # drain heavies for the next trial
            run_dequeues(s, s.pending())

    def test_equal_weights_round_robin_like(self):
        # n equal-weight queues backlogged; a brand-new queue arriving
        # late must be served within bound (S) = 1 + n * 2 dequeues.
        n = 8
        s = FairScheduler()
        for i in range(n):
            s.add_queue(f"q{i}", weight=1)
        for i in range(n):
            for t in range(100):
                s.enqueue(f"q{i}", t)
        s.add_queue("late", weight=1)
        bound = s.starvation_bound("late")
        self.assertEqual(bound, 1 + n * 2)
        s.enqueue("late", "marked")
        seen = 0
        while True:
            name, task = s.dequeue()
            seen += 1
            if task == "marked":
                break
            self.assertLessEqual(seen, bound)


class TestZeroWeight(unittest.TestCase):
    def test_zero_weight_never_scheduled(self):
        s = FairScheduler()
        s.add_queue("parked", weight=0)
        s.add_queue("live", weight=1)
        s.enqueue("parked", "p")
        for i in range(100):
            s.enqueue("live", i)
        order = run_dequeues(s, 200)
        self.assertEqual(order, ["live"] * 100)
        self.assertEqual(s.pending("parked"), 1)
        self.assertIsNone(s.starvation_bound("parked"))  # no finite bound

    def test_zero_weight_reactivation(self):
        s = FairScheduler()
        s.add_queue("parked", weight=0)
        s.enqueue("parked", "p1")
        s.enqueue("parked", "p2")
        self.assertIsNone(s.dequeue())  # only queue, but weight 0
        s.set_weight("parked", 1)
        self.assertEqual(s.dequeue(), ("parked", "p1"))
        self.assertEqual(s.dequeue(), ("parked", "p2"))

    def test_weight_can_be_parked_and_unparked_dynamically(self):
        s = FairScheduler()
        s.add_queue("a", weight=1)
        s.add_queue("b", weight=1)
        for i in range(100):
            s.enqueue("a", f"a{i}")
            s.enqueue("b", f"b{i}")
        s.set_weight("b", 0)
        order = run_dequeues(s, 50)
        self.assertEqual(order, ["a"] * 50)
        s.set_weight("b", 3)
        order = run_dequeues(s, 40)
        # b now has 3x the weight of a and a backlog: b dominates.
        self.assertGreater(order.count("b"), order.count("a"))


class TestInjectedClock(unittest.TestCase):
    def test_wait_stats_use_injected_time(self):
        clock = FakeClock()
        s = FairScheduler(time_fn=clock)
        s.add_queue("a")
        s.enqueue("a", "x")
        clock.advance(5.0)
        s.enqueue("a", "y")
        clock.advance(5.0)
        s.dequeue()  # x waited 10
        s.dequeue()  # y waited 5
        stats = s.stats()["queues"]["a"]
        self.assertEqual(stats["served"], 2)
        self.assertAlmostEqual(stats["avg_wait"], 7.5)

    def test_scheduling_is_time_independent(self):
        # Same arrivals, different clocks -> identical serve order.
        def build(clock):
            s = FairScheduler(time_fn=clock)
            for name, w in (("a", 1), ("b", 2)):
                s.add_queue(name, weight=w)
                for i in range(30):
                    s.enqueue(name, i)
            return s

        clock = FakeClock()
        s1 = build(clock)
        clock.advance(1e6)
        s2 = build(FakeClock())
        self.assertEqual(run_dequeues(s1, 60), run_dequeues(s2, 60))


if __name__ == "__main__":
    unittest.main(verbosity=2)
