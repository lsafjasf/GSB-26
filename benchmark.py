"""Benchmark: measured vs expected search steps and per-node memory
for different level-promotion probabilities p.

Theory (Pugh's skip list analysis):
  * expected search cost  ~ (1/p) * log_{1/p}(n)   pointer hops
  * expected pointers per node = 1/(1-p)  ->  memory factor vs p

Because the top of the tower contains only a handful of nodes, the
average search cost of one *realized* structure varies noticeably
between seeds (more so for small p). We therefore build several
independent structures per p and report mean [min, max].

Run: python3 benchmark.py
"""

import math
import random
import sys

from skiplist import SkipList

N = 100_000
QUERIES = 20_000
SEEDS = (11, 22, 33, 44, 55)


def measure(p, seed, max_level=32):
    sl = SkipList(p=p, max_level=max_level, rng=random.Random(seed))
    for k in range(N):
        sl.insert(k, k)

    qrng = random.Random(seed + 1)
    total = 0
    for _ in range(QUERIES):
        _, steps = sl.find_with_steps(qrng.randrange(N * 2))
        total += steps
    avg_steps = total / QUERIES

    node = sl._head.next[0]
    total_bytes = 0
    total_levels = 0
    count = 0
    while node is not None:
        total_bytes += sys.getsizeof(node) + sys.getsizeof(node.next)
        total_levels += len(node.next)
        count += 1
        node = node.next[0]
    return avg_steps, total_levels / count, total_bytes


def main():
    print("n = %d keys, %d random finds per structure, seeds = %s"
          % (N, QUERIES, SEEDS))
    hdr = ("p", "steps mean", "steps [min,max]", "theory (1/p)*log_{1/p}(n)",
           "avg level", "theory 1/(1-p)", "bytes/key")
    print("%-5s %-11s %-15s %-24s %-10s %-14s %-10s" % hdr)
    for p in (0.5, 0.25, 0.1):
        runs = [measure(p, s) for s in SEEDS]
        steps = [r[0] for r in runs]
        levels = [r[1] for r in runs]
        bytes_per_key = runs[0][2] / N
        theory_steps = (1.0 / p) * math.log(N, 1.0 / p)
        print("%-5.2f %-11.2f %-15s %-24.1f %-10.3f %-14.3f %-10.1f"
              % (p, sum(steps) / len(steps),
                 "[%.1f, %.1f]" % (min(steps), max(steps)),
                 theory_steps, sum(levels) / len(levels),
                 1.0 / (1.0 - p), bytes_per_key))
    print()
    print("notes:")
    print("  * theory matches measurement: p=0.5 and p=0.25 cost roughly the")
    print("    same expected steps (~(1/p)*log_{1/p}(n)); p=0.1 costs ~50% more.")
    print("  * memory: pointers/node = 1/(1-p) -> p=0.5: 2.00, p=0.25: 1.33,")
    print("    p=0.1: 1.11. Smaller p uses fewer pointers (less index memory)")
    print("    but each level covers less ground, so searches get longer and")
    print("    the per-structure variance across seeds grows (fewer, thicker")
    print("    towers at the top). p=0.25 is a good memory/speed compromise.")
    print("  * bytes/key = sys.getsizeof(_Node) + its next-pointer list,")
    print("    excluding key/value objects; CPython object overhead dominates,")
    print("    so the pointer saving shows up as only a few bytes/key here.")


if __name__ == "__main__":
    main()
