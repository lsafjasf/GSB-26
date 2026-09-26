"""调用次数对比 demo（确定性虚拟时钟，无需等待真实时间）。

运行：python3 demo.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from batchmerge import Batcher, VirtualClock


def main() -> None:
    n_unique = 80          # 80 个不同键
    repeats = 25           # 每个键在窗口内被并发请求 25 次
    total = n_unique * repeats

    downstream_calls = []

    def handler(keys, complete):
        downstream_calls.append(list(keys))
        return {k: f"value({k})" for k in keys}

    clock = VirtualClock()
    batcher = Batcher(handler, max_batch_size=100, window_ms=10, scheduler=clock)

    futures = [batcher.load(k) for _ in range(repeats) for k in range(n_unique)]
    clock.advance(10)  # 关闭窗口，触发一次批量提交

    assert all(f.done() for f in futures), "所有调用方都必须拿到结果，不得挂起"
    assert all(f.result() == f"value({k})"
               for f, k in zip(futures, [k for _ in range(repeats) for k in range(n_unique)]))

    naive = total
    merged = len(downstream_calls)
    keys_sent = sum(len(c) for c in downstream_calls)

    print("请求批处理 / 合并：下游调用次数对比")
    print("-" * 46)
    print(f"总请求量（调用方发起） : {total}")
    print(f"不同键数量             : {n_unique}（每键 {repeats} 次并发）")
    print(f"批大小上限 / 窗口      : 100 / 10ms")
    print("-" * 46)
    print(f"不合并（朴素直连）     : {naive} 次下游调用")
    print(f"开启批处理+合并        : {merged} 次下游调用（共下发 {keys_sent} 个去重键）")
    print(f"调用量下降             : {(1 - merged / naive) * 100:.2f}%")
    print(f"每次调用平均承载       : {total / merged:.0f} 个调用方请求")
    print("-" * 46)
    print("结果分发校验           : 全部 2000 个调用方均收到属于自己的正确结果 ✔")


if __name__ == "__main__":
    main()
