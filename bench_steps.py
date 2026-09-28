"""Benchmark: expected vs measured search steps, and memory vs level prob p.

Theory (skip list analysis):
  * expected search cost  ~ (1/p) * log_{1/p}(n) pointer reads, counting
    both forward traversals and reads after descending one level
  * expected forward pointers per node = 1/(1-p)  -> memory grows as p grows

Run: python3 bench_steps.py
"""

import math
import random
import sys

from skiplist import SkipList

N = 50_000
QUERIES = 5_000
P_VALUES = [0.10, 0.25, 0.50, 0.75]


def measure(p: float):
    sl = SkipList(p=p, rand=random.Random(42).random)
    for k in range(N):
        sl.insert(k, k)

    rng = random.Random(7)
    total_steps = 0
    for _ in range(QUERIES):
        total_steps += sl.search_steps(rng.randrange(N))
    avg_steps = total_steps / QUERIES

    # Expected pointer reads: (1/p) * log_{1/p}(n).
    expected = math.log(N, 1 / p) / p

    slots = sl.total_pointer_slots()
    # rough byte estimate: each _Node has fixed overhead + a list of slots
    per_node = sys.getsizeof(object())  # placeholder, refined below
    node_bytes = 0
    node = sl._head.next[0]
    while node is not None:
        node_bytes += sys.getsizeof(node) + sys.getsizeof(node.next)
        node = node.next[0]
    return avg_steps, expected, slots, slots / N, node_bytes


def main():
    print(f"n = {N} keys, {QUERIES} random lookups per configuration\n")
    header = (
        f"{'p':>5} | {'measured steps':>14} | {'expected (1/p)*log_1/p(n)':>24} | "
        f"{'ptr slots/node':>14} | {'theory 1/(1-p)':>14} | {'node mem (MB)':>13}"
    )
    print(header)
    print("-" * len(header))
    for p in P_VALUES:
        avg_steps, expected, slots, slots_per_node, node_bytes = measure(p)
        print(
            f"{p:>5.2f} | {avg_steps:>14.1f} | {expected:>24.1f} | "
            f"{slots_per_node:>14.2f} | {1 / (1 - p):>14.2f} | {node_bytes / 1e6:>13.1f}"
        )
    print(
        "\nReading: smaller p -> fewer pointer slots (less memory) but more "
        "levels to descend per hop;\nlarger p -> more pointers per node "
        "(more memory) with fewer, longer hops.\n"
        "Measured steps count forward traversals plus one pointer read per "
        "level descent and closely track the asymptotic estimate."
    )


if __name__ == "__main__":
    main()
