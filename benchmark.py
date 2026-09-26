"""Benchmarks: incremental update vs full rebuild; proof length vs height."""

import os
import random
import time

from merkle import MerkleTree, tree_height


def make_blocks(n, size=64, seed=1):
    rng = random.Random(seed)
    return [rng.randbytes(size) for _ in range(n)]


def time_rebuild(blocks, repeats=3):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        MerkleTree(blocks)
        best = min(best, time.perf_counter() - t0)
    return best


def bench_update(n, updates):
    blocks = make_blocks(n)
    tree = MerkleTree(blocks)
    rng = random.Random(99)
    indices = [rng.randrange(n) for _ in range(updates)]
    new_data = [os.urandom(64) for _ in range(updates)]

    t0 = time.perf_counter()
    for i, data in zip(indices, new_data):
        tree.update(i, data)
    t_inc = time.perf_counter() - t0

    t_one = time_rebuild(blocks)  # one full rebuild; rebuild cost is ~constant per op
    t_rebuild = t_one * updates

    print(f"n={n:>7,}  updates={updates:>5}  "
          f"incremental: total={t_inc * 1e3:9.2f} ms ({t_inc / updates * 1e6:7.2f} us/op)  "
          f"rebuild: total={t_rebuild * 1e3:11.2f} ms ({t_one * 1e6:11.2f} us/op)  "
          f"speedup={t_rebuild / t_inc:9.1f}x")


def bench_append(n, appends):
    blocks = make_blocks(n)
    tree = MerkleTree(blocks)
    new_data = [os.urandom(64) for _ in range(appends)]

    t0 = time.perf_counter()
    for data in new_data:
        tree.append(data)
    t_inc = time.perf_counter() - t0

    t_one = time_rebuild(blocks)
    t_rebuild = t_one * appends

    print(f"n={n:>7,}  appends={appends:>5}   "
          f"incremental: total={t_inc * 1e3:9.2f} ms ({t_inc / appends * 1e6:7.2f} us/op)  "
          f"rebuild: total={t_rebuild * 1e3:11.2f} ms ({t_one * 1e6:11.2f} us/op)  "
          f"speedup={t_rebuild / t_inc:9.1f}x")


def proof_shape():
    print(f"{'leaves':>10} {'height':>6} {'min_proof':>9} {'max_proof':>9} {'avg_proof':>9}")
    for n in (1, 2, 3, 4, 5, 8, 9, 16, 100, 1_000, 10_000, 16_384, 100_000):
        tree = MerkleTree(make_blocks(n, size=8))
        lengths = [len(tree.prove(i).siblings) for i in range(n)]
        print(f"{n:>10,} {tree_height(n):>6} {min(lengths):>9} {max(lengths):>9} "
              f"{sum(lengths) / len(lengths):>9.2f}")


if __name__ == "__main__":
    print("== incremental update vs full rebuild (modify one block) ==")
    bench_update(10_000, 200)
    bench_update(100_000, 200)
    bench_update(100_000, 2_000)
    print()
    print("== incremental append vs full rebuild ==")
    bench_append(50_000, 500)
    print()
    print("== proof length vs tree height ==")
    proof_shape()
