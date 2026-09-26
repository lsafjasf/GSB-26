"""性能自测：十万级任务树的创建与取消传播耗时。

运行：python3 perf_tasktree.py
"""

import gc
import time
from tasktree import State, TaskNode


def build_flat(n: int) -> TaskNode:
    root = TaskNode("root")
    root.start()
    for i in range(n):
        c = root.create_child(f"task-{i}")
        c.start()
    return root


def build_deep(depth: int) -> TaskNode:
    root = TaskNode("root")
    root.start()
    node = root
    for i in range(depth):
        node = node.create_child(f"level-{i}")
        node.start()
    return root


def count_cancelled(root: TaskNode) -> int:
    return root.summary()["cancelled"]


def bench(label, fn):
    gc.collect()
    t0 = time.perf_counter()
    result = fn()
    dt = time.perf_counter() - t0
    print(f"{label:<44} {dt*1000:>10.1f} ms")
    return result, dt


def main():
    N = 100_000

    print(f"== 扁平树：1 个根 + {N:,} 个子任务 ==")
    root, t_create = bench(f"创建 {N:,} 个任务", lambda: build_flat(N))
    _, t_cancel = bench("取消根节点（传播到全部后代）", root.cancel)
    assert count_cancelled(root) == N + 1
    print(f"   -> 创建 {N/t_create/1e6:.2f}M 任务/秒, "
          f"取消传播 {N/t_cancel/1e6:.2f}M 任务/秒")
    del root

    print()
    print(f"== 深层链：{N:,} 层嵌套 ==")
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), N * 4))
    deep, t_create_d = bench(f"创建 {N:,} 层深链", lambda: build_deep(N))
    _, t_cancel_d = bench("取消根节点（沿链传播）", deep.cancel)
    assert count_cancelled(deep) == N + 1
    print(f"   -> 创建 {N/t_create_d/1e6:.2f}M 任务/秒, "
          f"取消传播 {N/t_cancel_d/1e6:.2f}M 任务/秒")
    del deep

    print()
    print("== 混合树：100 棵子树 x 1000 任务 ==")
    def build_mixed():
        r = TaskNode("root"); r.start()
        for i in range(100):
            sub = r.create_child(f"sub-{i}"); sub.start()
            for j in range(1000):
                c = sub.create_child(f"leaf-{j}"); c.start()
        return r
    mixed, _ = bench("创建 100x1000 混合树", build_mixed)
    bench("取消根节点（传播到 100,101 个节点）", mixed.cancel)
    assert count_cancelled(mixed) == 100_101


if __name__ == "__main__":
    main()
