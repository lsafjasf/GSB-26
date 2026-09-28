"""性能自测：十万级任务树的创建、取消传播与汇总遍历耗时。

运行：python3 perf_tasktree.py

全程使用默认递归上限（sys.getrecursionlimit()，通常为 1000），
不调用 sys.setrecursionlimit；取消传播与汇总遍历均为显式栈迭代
实现，深链深度不再受递归层数限制。
"""

import gc
import sys
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


def iter_nodes(root: TaskNode):
    """显式栈先序遍历，断言辅助：遍历本身也不引入递归。"""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


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
    default_limit = sys.getrecursionlimit()
    print(f"递归上限（保持默认，脚本全程不改动）：{default_limit}")

    print(f"== 扁平树：1 个根 + {N:,} 个子任务 ==")
    root, t_create = bench(f"创建 {N:,} 个任务", lambda: build_flat(N))
    _, t_cancel = bench("取消根节点（传播到全部后代）", root.cancel)
    assert count_cancelled(root) == N + 1
    assert all(n.state is State.CANCELLED for n in iter_nodes(root))
    print(f"   -> 创建 {N/t_create/1e6:.2f}M 任务/秒, "
          f"取消传播 {N/t_cancel/1e6:.2f}M 任务/秒")
    del root

    print()
    print(f"== 深层链：{N:,} 层嵌套（深度是默认递归上限的 "
          f"{N // default_limit} 倍以上）==")
    deep, t_create_d = bench(f"创建 {N:,} 层深链", lambda: build_deep(N))
    s_before, t_summary_d = bench("取消前汇总遍历（沿链走一遍）", deep.summary)
    assert s_before["total"] == N + 1
    assert s_before["counts"][State.RUNNING.value] == N + 1
    assert s_before["cancelled"] == 0
    _, t_cancel_d = bench("取消根节点（沿链传播）", deep.cancel)
    assert count_cancelled(deep) == N + 1
    assert all(n.state is State.CANCELLED for n in iter_nodes(deep))
    s_after, _ = bench("取消后再次汇总（沿链走一遍）", deep.summary)
    assert s_after["total"] == N + 1
    assert s_after["cancelled"] == N + 1
    assert s_after["failed"] == 0
    print(f"   -> 创建 {N/t_create_d/1e6:.2f}M 任务/秒, "
          f"取消传播 {N/t_cancel_d/1e6:.2f}M 任务/秒, "
          f"汇总遍历 {N/t_summary_d/1e6:.2f}M 节点/秒")
    print("   -> 正确性断言：全部 N+1 个节点均为 CANCELLED，汇总计数一致")
    del deep

    print()
    print("== 混合树：100 棵子树 x 1000 任务 ==")
    def build_mixed():
        r = TaskNode("root"); r.start()
        for i in range(100):
            sub = r.create_child(f"sub-{i}"); sub.start()
            for j in range(1000):
                c = sub.create_child(f"leaf-{j}")
                c.start()
        return r
    mixed, _ = bench("创建 100x1000 混合树", build_mixed)
    bench("取消根节点（传播到 100,101 个节点）", mixed.cancel)
    assert count_cancelled(mixed) == 100_101

    print()
    print(f"== 结论：默认递归上限 {default_limit} 下完成 {N:,} 层深链的"
          "创建/汇总/取消，全程无 RecursionError ==")
    assert sys.getrecursionlimit() == default_limit, "脚本不得改动递归上限"


if __name__ == "__main__":
    main()
