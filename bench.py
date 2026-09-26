"""吞吐对比 + 热点分布基准。

运行：python3 bench.py

对比三方：
- BuggyShardedCounter（缺陷版，无锁，结果不正确，仅作参考）
- SingleLockCounter（单锁串行基线）
- FixedShardedCounter（修复版，分片锁 + 线程槽位散列）
"""

import threading
import time

from buggy_counter import BuggyShardedCounter
from fixed_counter import FixedShardedCounter

THREADS = 8
OPS_PER_THREAD = 50_000


class SingleLockCounter:
    """单锁串行基线：同一套分片结构退化为一把全局锁（公平对比）。"""

    def __init__(self, shards=16):
        self.n = shards
        self.shards = [0] * shards
        self._lock = threading.Lock()

    def increment(self, key, delta=1):
        i = hash(key) % self.n
        with self._lock:
            self.shards[i] += delta

    def read(self):
        with self._lock:
            return sum(self.shards)


def bench(counter, key_fn, threads=THREADS, ops=OPS_PER_THREAD):
    barrier = threading.Barrier(threads)

    def worker(tid):
        key = key_fn(tid)
        barrier.wait()
        for _ in range(ops):
            counter.increment(key)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    start = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    elapsed = time.perf_counter() - start
    total = threads * ops
    return total / elapsed, counter.read(), total


def main():
    print(f"threads={THREADS} ops/thread={OPS_PER_THREAD} "
          f"(CPython {__import__('sys').version.split()[0]}, GIL 开启)\n")

    scenarios = [
        ("热点键(所有线程同一 key)", lambda tid: "hot-key"),
        ("分散键(每线程不同 key)", lambda tid: f"key-{tid}"),
    ]
    makers = [
        ("buggy(无锁,不正确)", lambda: BuggyShardedCounter(shards=16, yield_on_write=False)),
        ("single-lock(串行基线)", SingleLockCounter),
        ("fixed(分片锁+线程散列)", lambda: FixedShardedCounter(stripes=64)),
    ]

    for scen_name, key_fn in scenarios:
        print(f"== {scen_name} ==")
        for name, make in makers:
            c = make()
            ops_sec, got, want = bench(c, key_fn)
            ok = "OK " if got == want else f"WRONG(got={got})"
            print(f"  {name:<26} {ops_sec/1e6:7.2f} M ops/s  正确性:{ok}")
        print()

    # 热点分布数据
    print("== 热点键分片分布（8 线程 x 5000 次自增同一 key）==")
    per = 5000
    buggy = BuggyShardedCounter(shards=16, yield_on_write=False)
    bench(buggy, lambda tid: "hot-key", ops=per)
    b_loads = [v for v in buggy.shards if v > 0]
    print(f"  buggy : 非零分片 {len(b_loads)}/{buggy.n}  分布={b_loads} "
          f"(总数 {sum(b_loads)} / 应得 {THREADS*per}，丢失 {THREADS*per - sum(b_loads)})")

    fixed = FixedShardedCounter(stripes=64)
    bench(fixed, lambda tid: "hot-key", ops=per)
    f_loads = sorted((v for v in fixed.stripe_loads() if v > 0), reverse=True)
    top = f_loads[0] / sum(f_loads)
    print(f"  fixed : 非零分片 {len(f_loads)}/{fixed.n}  分布={f_loads} "
          f"最大占比 {top:.1%} (总数 {sum(f_loads)} / 应得 {THREADS*per}，无丢失)")

    # 线程数扩展性（热点键）
    print("\n== 线程扩展性（热点键，ops/s）==")
    print(f"  {'threads':>7}  {'single-lock':>14}  {'fixed':>14}")
    for nt in (1, 2, 4, 8):
        sl_ops, _, _ = bench(SingleLockCounter(), lambda tid: "hot-key", threads=nt)
        fx_ops, _, _ = bench(FixedShardedCounter(stripes=64), lambda tid: "hot-key",
                             threads=nt)
        print(f"  {nt:>7}  {sl_ops/1e6:>12.2f}M  {fx_ops/1e6:>12.2f}M")


if __name__ == "__main__":
    main()
