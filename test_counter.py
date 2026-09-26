"""复现四类缺陷的回归测试 + 修复后的守恒/单调性断言。

运行：python3 -m unittest test_counter -v

BuggyReproTests：断言"缺陷被观察到"，用于稳定复现现网问题。
FixedCounterTests：断言修复后的不变量，必须通过。
"""

import threading
import time
import unittest

from buggy_counter import BuggyShardedCounter
from fixed_counter import FixedShardedCounter

THREADS = 8
INCR_PER_THREAD = 2000


def run_increments(counter, keys_per_thread, nthreads=THREADS, per_thread=INCR_PER_THREAD,
                   barrier=None):
    """启动 nthreads 个线程并发自增，返回 (issued, issued_lock, threads)。"""
    issued = [0]
    issued_lock = threading.Lock()
    if barrier is None:
        barrier = threading.Barrier(nthreads)

    def worker(tid):
        key = keys_per_thread(tid)
        barrier.wait()
        for _ in range(per_thread):
            with issued_lock:
                issued[0] += 1
            counter.increment(key)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(nthreads)]
    for t in threads:
        t.start()
    return issued, issued_lock, threads


def join_all(threads):
    for t in threads:
        t.join()


class BuggyReproTests(unittest.TestCase):
    """每个用例稳定复现一类现网缺陷（断言缺陷现象被观察到）。"""

    def test_bug1_lost_updates_under_concurrency(self):
        """高频自增：最终总数 < 实际增量次数（丢失更新）。"""
        expected = THREADS * INCR_PER_THREAD
        counter = BuggyShardedCounter(shards=16)
        _, _, threads = run_increments(counter, lambda t: f"key-{t % 8}")
        join_all(threads)
        lost = expected - counter.read()
        print(f"\n[bug1] expected={expected} actual={counter.read()} lost={lost}")
        self.assertGreater(lost, 0, "未复现丢失更新")

    def test_bug2_read_negative_or_regress(self):
        """边自增边 merge 边读取：读到负数或回退。"""
        counter = BuggyShardedCounter(shards=16)
        stop = threading.Event()
        observed = []

        def incr_worker(tid):
            while not stop.is_set():
                counter.increment(f"key-{tid % 4}")

        def merge_worker():
            while not stop.is_set():
                counter.merge()

        workers = [threading.Thread(target=incr_worker, args=(i,)) for i in range(4)]
        workers += [threading.Thread(target=merge_worker) for _ in range(2)]
        for w in workers:
            w.start()

        prev = 0
        deadline = time.time() + 5.0
        while time.time() < deadline and not observed:
            cur = counter.read()
            if cur < 0:
                observed.append(("negative", cur))
            elif cur < prev:
                observed.append(("regress", prev, cur))
            prev = max(prev, cur)
        stop.set()
        for w in workers:
            w.join()
        print(f"\n[bug2] observed={observed}")
        self.assertTrue(observed, "未复现负数/回退读取")

    def test_bug3_merge_concurrent_double_count_or_loss(self):
        """merge 与自增并发：最终总数 != 实际增量次数（重复累加或吞掉）。"""
        expected = THREADS * INCR_PER_THREAD
        counter = BuggyShardedCounter(shards=16)
        issued, _, threads = run_increments(counter, lambda t: f"key-{t % 8}")
        mergers = [threading.Thread(target=counter.merge) for _ in range(2)]
        for m in mergers:
            m.start()
        join_all(threads)
        join_all(mergers)
        final = counter.read()
        print(f"\n[bug3] issued={issued[0]} final={final} diff={final - issued[0]}")
        self.assertEqual(issued[0], expected)
        self.assertNotEqual(final, expected, "未复现 merge 并发导致的计数不守恒")

    def test_bug4_hotkey_single_shard(self):
        """热点键：所有线程压到同一个分片。"""
        counter = BuggyShardedCounter(shards=16)
        _, _, threads = run_increments(counter, lambda t: "hot-key", per_thread=200)
        join_all(threads)
        used = [i for i, v in enumerate(counter.shards) if v != 0]
        print(f"\n[bug4] hot-key shards used={used} (of {counter.n})")
        self.assertEqual(len(used), 1, "热点键未集中到单分片，缺陷未复现")


class FixedCounterTests(unittest.TestCase):
    """修复后的不变量断言。"""

    def test_conservation_exact_total(self):
        """最终总数严格等于增量次数；运行中 0 <= read <= 已发起增量数。"""
        counter = FixedShardedCounter(stripes=64)
        expected = THREADS * INCR_PER_THREAD
        issued, issued_lock, threads = run_increments(counter, lambda t: f"key-{t % 8}")

        violations = []
        while any(t.is_alive() for t in threads):
            r = counter.read()
            with issued_lock:
                iss = issued[0]
            if not (0 <= r <= iss):
                violations.append((r, iss))
        join_all(threads)
        self.assertEqual(violations, [], f"不变量被破坏: {violations[:3]}")
        self.assertEqual(counter.read(), expected, "最终总数不等于增量次数")

    def test_monotonic_reads_no_negative(self):
        """读取单调非降且不为负（边自增边读取）。"""
        counter = FixedShardedCounter(stripes=64)
        _, _, threads = run_increments(counter, lambda t: f"key-{t % 4}")

        samples = []
        while any(t.is_alive() for t in threads):
            samples.append(counter.read())
        join_all(threads)
        samples.append(counter.read())
        self.assertTrue(all(s >= 0 for s in samples), "读到负数")
        for a, b in zip(samples, samples[1:]):
            self.assertGreaterEqual(b, a, f"读取回退: {a} -> {b}")

    def test_compact_concurrent_conservation(self):
        """compact（合并）与自增并发：不丢失、不重复，最终严格相等。"""
        counter = FixedShardedCounter(stripes=64)
        expected = THREADS * INCR_PER_THREAD
        _, _, threads = run_increments(counter, lambda t: f"key-{t % 8}")

        compactor = threading.Thread(
            target=lambda: [counter.compact() for _ in range(50)])
        compactor.start()
        join_all(threads)
        compactor.join()
        counter.compact()
        self.assertEqual(counter.read(), expected, "compact 并发后总数不守恒")

    def test_hotkey_spread_across_stripes(self):
        """热点键被散到多个分片，不再集中。"""
        counter = FixedShardedCounter(stripes=64)
        per_thread = 500
        _, _, threads = run_increments(counter, lambda t: "hot-key", per_thread=per_thread)
        join_all(threads)
        loads = counter.stripe_loads()
        used = [v for v in loads if v > 0]
        total = sum(used)
        top = max(used) / total
        print(f"\n[fixed-hot] stripes used={len(used)}/{counter.n} "
              f"top-share={top:.1%} loads={sorted(used, reverse=True)}")
        self.assertEqual(counter.read(), THREADS * per_thread)
        self.assertGreaterEqual(len(used), THREADS // 2, "热点键仍过度集中")
        self.assertLess(top, 0.6, "最大分片占比过高")


if __name__ == "__main__":
    unittest.main(verbosity=2)
