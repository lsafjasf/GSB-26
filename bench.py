"""修复前后吞吐对比：多线程并发投递批次，统计样本吞吐（samples/sec）。

BuggyAggregator 以 yield_on_write=False 运行（关闭测试用的主动让权，
等价于“真实”裸写性能）；Aggregator 为修复后实现。
两者共享同一个聚合器实例以体现真实锁竞争。
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from aggregator import Aggregator
from aggregator_buggy import BuggyAggregator

BATCH_SIZE = 8
DIMS = ["dimA", "dimB", "dimC", "dimD"]


def make_samples(count):
    return [(DIMS[i % len(DIMS)], 1) for i in range(count)]


def bench(apply, threads, batches_per_thread, with_sid):
    """apply(batch_id, samples) 为归一化后的投递函数；批次预先构建，不计入耗时。"""
    if with_sid:
        batches = [[(f"s{seq}-{i}", d, n) for i, (d, n) in enumerate(make_samples(BATCH_SIZE))]
                   for seq in range(batches_per_thread)]
    else:
        batches = [make_samples(BATCH_SIZE) for _ in range(batches_per_thread)]
    barrier = threading.Barrier(threads)

    def worker(tid):
        barrier.wait()
        for seq in range(batches_per_thread):
            apply(tid * batches_per_thread + seq, batches[seq])

    start = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    elapsed = time.perf_counter() - start
    return threads * batches_per_thread * BATCH_SIZE / elapsed, elapsed


def main():
    batches_per_thread = 30000
    reps = 3
    print(f"workload: batch_size={BATCH_SIZE}, batches/thread={batches_per_thread}, reps={reps} (median)")
    print(f"{'impl':<8}{'threads':>8}{'samples/s':>16}{'elapsed(s)':>12}")
    print("-" * 44)
    for threads in (1, 4, 8, 16):
        buggy = BuggyAggregator(yield_on_write=False)
        fixed = Aggregator()
        # buggy 接口为 (sid, dim, n) 三元组；fixed 为 (dim, n) 二元组
        buggy_apply = buggy.apply_batch
        fixed_apply = fixed.apply_batch
        for name, apply, with_sid in (("buggy", buggy_apply, True), ("fixed", fixed_apply, False)):
            bench(apply, threads, batches_per_thread // 10, with_sid)  # 预热
            runs = sorted(bench(apply, threads, batches_per_thread, with_sid) for _ in range(reps))
            rate, elapsed = runs[len(runs) // 2]
            print(f"{name:<8}{threads:>8}{rate:>16,.0f}{elapsed:>12.3f}")


if __name__ == "__main__":
    main()
