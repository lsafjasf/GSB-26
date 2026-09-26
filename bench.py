"""Benchmark: create and cancel 100k tasks (flat and balanced trees)."""

import gc
import time

from tasktree import TaskNode

N = 100_000


def bench_flat(n):
    root = TaskNode("root")
    t0 = time.perf_counter()
    for i in range(n):
        root.spawn(f"c{i}")
    t1 = time.perf_counter()
    root.cancel()
    t2 = time.perf_counter()
    summary = root.summary()
    assert summary.cancelled == n + 1, summary
    return t1 - t0, t2 - t1


def bench_balanced(n):
    # fan-out 10, depth 6 -> 1_111_111 nodes is too many; pick depth so
    # total >= n: depth 5 -> 111_111 nodes.
    root = TaskNode("root")
    t0 = time.perf_counter()
    level = [root]
    total = 1
    counter = 0
    while total < n:
        nxt = []
        for node in level:
            for _ in range(10):
                counter += 1
                nxt.append(node.spawn(f"n{counter}"))
        total += len(nxt)
        level = nxt
    t1 = time.perf_counter()
    root.cancel()
    t2 = time.perf_counter()
    summary = root.summary()
    assert summary.cancelled == total, summary
    return t1 - t0, t2 - t1, total


def main():
    gc.disable()
    try:
        c, x = bench_flat(N)
        print(f"flat tree     : {N:>8,} tasks | create {c*1e3:8.2f} ms "
              f"({c/N*1e6:6.3f} us/task) | cancel {x*1e3:8.2f} ms "
              f"({x/(N+1)*1e6:6.3f} us/task)")
        c, x, total = bench_balanced(N)
        print(f"balanced tree : {total:>8,} tasks | create {c*1e3:8.2f} ms "
              f"({c/total*1e6:6.3f} us/task) | cancel {x*1e3:8.2f} ms "
              f"({x/total*1e6:6.3f} us/task)")
    finally:
        gc.enable()


if __name__ == "__main__":
    main()
