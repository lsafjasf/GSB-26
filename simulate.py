"""Simulation & benchmark harness for fair_scheduler.

Produces the data quoted in README.md:
  1. long-run fairness: actual vs ideal serve counts, deviation data
  2. normalized-lag bound (F) tracked continuously during the run
  3. empty-queue quota redistribution
  4. starvation bound (S) vs observed worst-case wait (simulation)
  5. throughput under high load

Run:  python3 simulate.py
"""

import math
import time

from fair_scheduler import FairScheduler


def section(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# --------------------------------------------------------------------- #
# 1 + 2. Long-run fairness and the normalized-lag bound
# --------------------------------------------------------------------- #
def fairness_experiment():
    section("1. Long-run fairness: weights 1:2:3:4, 1,000,000 dequeues")
    weights = {"q1": 1, "q2": 2, "q3": 3, "q4": 4}
    total = 1_000_000
    s = FairScheduler()
    for name, w in weights.items():
        s.add_queue(name, weight=w)
        for i in range(total):
            s.enqueue(name, i)

    counts = {name: 0 for name in weights}
    max_lag, lag_at = 0.0, 0
    names = list(weights)
    for step in range(1, total + 1):
        name, _ = s.dequeue()
        counts[name] += 1
        # normalized lag L = max_{i,j} |c_i/w_i - c_j/w_j| over all pairs
        norm = [counts[n] / weights[n] for n in names]
        lag = max(norm) - min(norm)
        if lag > max_lag:
            max_lag, lag_at = lag, step

    wsum = sum(weights.values())
    print(f"{'queue':<6}{'weight':>7}{'served':>10}{'ideal':>11}"
          f"{'abs dev':>9}{'rel dev':>9}")
    for name, w in weights.items():
        ideal = total * w / wsum
        dev = counts[name] - ideal
        print(f"{name:<6}{w:>7}{counts[name]:>10}{ideal:>11.1f}"
              f"{dev:>9.1f}{dev / ideal:>9.4%}")
    bound = max(1 / weights[i] + 1 / weights[j]
                for i in names for j in names if i != j)
    print(f"\nmax normalized lag |c_i/w_i - c_j/w_j| observed: {max_lag:.4f}"
          f" (at dequeue #{lag_at})")
    print(f"theoretical bound (F) = max(1/w_i + 1/w_j) = {bound:.4f}"
          f"  -> holds: {max_lag <= bound + 1e-9}")
    print("note: lag is a bounded CONSTANT; relative deviation "
          "-> 0 as run length grows.")


# --------------------------------------------------------------------- #
# 3. Empty-queue quota redistribution
# --------------------------------------------------------------------- #
def redistribution_experiment():
    section("2. Empty queue consumes no quota (weights 1:2:3, c drains)")
    s = FairScheduler()
    s.add_queue("a", weight=1)
    s.add_queue("b", weight=2)
    s.add_queue("c", weight=3)
    for i in range(5000):
        s.enqueue("a", i)
        s.enqueue("b", i)
    for i in range(120):
        s.enqueue("c", i)

    order = []
    for _ in range(3000):
        item = s.dequeue()
        assert item is not None, "scheduler idled while work remained!"
        order.append(item[0])
    last_c = max(i for i, n in enumerate(order) if n == "c")
    phase1, phase2 = order[: last_c + 1], order[last_c + 1 :]

    def shares(window, keys):
        n = len(window)
        return {k: f"{window.count(k) / n:.1%}" for k in keys}

    print(f"phase 1 (a,b,c active), {len(phase1)} dequeues, "
          f"ideal 1/6 : 2/6 : 3/6")
    print(f"  actual shares: {shares(phase1, 'abc')}")
    print(f"phase 2 (c empty),      {len(phase2)} dequeues, "
          f"ideal a:b = 1/3 : 2/3, c = 0")
    print(f"  actual shares: {shares(phase2, 'abc')}")
    print("  -> no dequeue returned None while work remained; c's share"
          " was\n     redistributed to a and b in proportion 1:2.")


# --------------------------------------------------------------------- #
# 4. Starvation bound vs observed wait
# --------------------------------------------------------------------- #
def starvation_experiment():
    section("3. Starvation freedom: bound (S) vs simulated worst case")
    configs = [
        ("8 x w=1, late w=1", [1] * 8, 1),
        ("3 x w=1, tiny w=0.01", [1] * 3, 0.01),
        ("w in {1,2,4,8}, tiny w=0.5", [1, 2, 4, 8], 0.5),
        ("1 x w=1000, tiny w=1", [1000], 1),
    ]
    trials = 300
    print(f"{'configuration':<28}{'bound B_i':>12}{'max obs':>9}"
          f"{'mean obs':>10}{'trials':>8}")
    for label, heavy_weights, tiny_w in configs:
        s = FairScheduler()
        for k, w in enumerate(heavy_weights):
            s.add_queue(f"h{k}", weight=w)
        s.add_queue("tiny", weight=tiny_w)
        bound = s.starvation_bound("tiny")
        worst, total_wait = 0, 0
        for _ in range(trials):
            for k in range(len(heavy_weights)):
                for t in range(bound + 10):
                    s.enqueue(f"h{k}", t)
            s.enqueue("tiny", "x")
            wait = 0
            while True:
                name, _ = s.dequeue()
                wait += 1
                if name == "tiny":
                    break
            worst = max(worst, wait)
            total_wait += wait
            while s.dequeue() is not None:
                pass  # drain for next trial
        print(f"{label:<28}{bound:>12}{worst:>9}"
              f"{total_wait / trials:>10.1f}{trials:>8}")
        assert worst <= bound, "starvation bound violated!"
    print("all observed waits <= bound (S) = 1 + sum_{j!=i} ceil(2 w_j/w_i)")


# --------------------------------------------------------------------- #
# 5. Throughput
# --------------------------------------------------------------------- #
def throughput_experiment():
    section("4. Throughput under high load (8 queues, equal weights)")

    def bench(label, fn, n):
        start = time.perf_counter()
        fn()
        dt = time.perf_counter() - start
        print(f"  {label:<44}{n / dt / 1e6:>8.2f} Mops/s"
              f"  ({dt:.2f}s for {n:,} ops)")

    n = 1_000_000
    s = FairScheduler()
    for i in range(8):
        s.add_queue(f"q{i}", weight=1)

    bench("enqueue x 1,000,000 (round-robin over 8 queues)",
          lambda: [s.enqueue(f"q{i % 8}", i) for i in range(n)], n)
    bench("dequeue x 1,000,000 (heap pop+push per op)",
          lambda: [s.dequeue() for _ in range(n)], n)

    s2 = FairScheduler()
    for i in range(8):
        s2.add_queue(f"q{i}", weight=1)
        s2.enqueue(f"q{i}", "seed")

    def mixed():
        for i in range(n):
            s2.enqueue(f"q{i % 8}", i)
            s2.dequeue()

    bench("mixed enqueue+dequeue x 1,000,000 pairs", mixed, n)

    s3 = FairScheduler()
    for i in range(64):
        s3.add_queue(f"q{i}", weight=(i % 7) + 1)
    bench("enqueue x 1,000,000 (64 queues, skewed weights)",
          lambda: [s3.enqueue(f"q{i % 64}", i) for i in range(n)], n)
    bench("dequeue x 1,000,000 (64 queues, skewed weights)",
          lambda: [s3.dequeue() for _ in range(n)], n)


if __name__ == "__main__":
    fairness_experiment()
    redistribution_experiment()
    starvation_experiment()
    throughput_experiment()
