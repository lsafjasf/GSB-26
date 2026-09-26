"""吞吐对比：缺陷版（无锁、结果错误）vs 修复版（有锁、结果正确）。

运行：python3 bench.py
"""

import threading
import time

from aggregator import Aggregator
from aggregator_buggy import BuggyAggregator

THREADS = 8
BATCHES_PER_THREAD = 5000
SAMPLES_PER_BATCH = 10
DIMS = ["dim_%d" % i for i in range(8)]


def bench(agg, label, threads=THREADS):
    def worker(tid):
        for i in range(BATCHES_PER_THREAD):
            batch = [
                (DIMS[(i + j) % len(DIMS)], (i + j) % 5 + 1)
                for j in range(SAMPLES_PER_BATCH)
            ]
            agg.apply_batch("t%d-b%d" % (tid, i), batch)

    start = time.perf_counter()
    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    elapsed = time.perf_counter() - start

    batches = threads * BATCHES_PER_THREAD
    samples = batches * SAMPLES_PER_BATCH
    total = agg.snapshot()[1] if hasattr(agg, "snapshot") else agg.total
    expected = sum(
        sum((i + j) % 5 + 1 for j in range(SAMPLES_PER_BATCH))
        for i in range(BATCHES_PER_THREAD)
    ) * threads
    print(
        "%-24s %6.3fs  %9.0f 批/秒  %10.0f 样本/秒  total=%d (期望 %d, %s)"
        % (
            label,
            elapsed,
            batches / elapsed,
            samples / elapsed,
            total,
            expected,
            "正确" if total == expected else "错误!",
        )
    )
    return batches / elapsed


if __name__ == "__main__":
    print("线程=%d, 每线程批次=%d, 每批样本=%d\n" % (THREADS, BATCHES_PER_THREAD, SAMPLES_PER_BATCH))
    buggy_rate = bench(BuggyAggregator(), "缺陷版(无锁) 8线程")
    fixed_rate = bench(Aggregator(), "修复版(单锁) 8线程")
    fixed_1t = bench(Aggregator(), "修复版(单锁) 1线程", threads=1)
    print("\n8 线程下修复版吞吐为缺陷版的 %.1f%%（锁竞争所致）" % (fixed_rate / buggy_rate * 100))
    print("1 线程下修复版为 %9.0f 批/秒：锁本身开销很小，差距主要来自 8 线程抢同一把锁"
          % fixed_1t)
