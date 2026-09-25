"""double_buffer 自测：正确性 + 回收时机断言 + 内存/延迟数据。

运行：python3 test_double_buffer.py -v
"""

import gc
import statistics
import threading
import time
import tracemalloc
import unittest

from double_buffer import DoubleBuffer


def make_payload(version_id, n_keys=200):
    """构造自带校验和的数据，读者可验证看到的是完整一致的版本。"""
    data = {f"key_{i}": f"value_{version_id}_{i}" for i in range(n_keys)}
    checksum = sum(hash(v) for v in data.values())
    return {"version": version_id, "data": data, "checksum": checksum}


def check_consistent(payload):
    """读者侧一致性校验：版本号、内容、校验和必须来自同一版本。"""
    data, checksum = payload["data"], payload["checksum"]
    version = payload["version"]
    for k, v in data.items():
        assert v == f"value_{version}_{k.split('_')[1]}", "torn read!"
    assert sum(hash(v) for v in data.values()) == checksum, "checksum mismatch"
    return len(data)


class TestCorrectness(unittest.TestCase):
    def test_single_publish(self):
        db = DoubleBuffer(make_payload(0))
        with db.acquire() as g:
            self.assertEqual(g.data["version"], 0)
        db.publish(make_payload(1))
        with db.acquire() as g:
            self.assertEqual(g.data["version"], 1)
        self.assertEqual(db.stats()["publish_count"], 1)

    def test_rapid_sequential_publishes(self):
        db = DoubleBuffer(make_payload(0))
        for i in range(1, 1001):
            db.publish(make_payload(i))
        with db.acquire() as g:
            self.assertEqual(g.data["version"], 1000)
        s = db.stats()
        # 无读者时回收即时：退休未回收数归零，内存有界
        self.assertEqual(s["pending_retired"], 0)
        self.assertEqual(s["reclaimed_count"], 1000)

    def test_publish_empty_data(self):
        db = DoubleBuffer()
        for empty in (None, {}, [], "", 0):
            db.publish(empty)
            with db.acquire() as g:
                self.assertEqual(g.data, empty)

    def test_read_during_publish_sees_complete_version(self):
        """读与发布同时开始：读者要么看到完整旧版本，要么看到完整新版本。"""
        db = DoubleBuffer(make_payload(0))
        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    with db.acquire() as g:
                        check_consistent(g.data)
            except AssertionError as e:
                errors.append(e)

        readers = [threading.Thread(target=reader) for _ in range(8)]
        for t in readers:
            t.start()
        for i in range(1, 501):
            db.publish(make_payload(i))
        stop.set()
        for t in readers:
            t.join()
        self.assertEqual(errors, [])

    def test_publish_and_read_start_simultaneously(self):
        """发布与读严格同时起跑（barrier 对齐）。"""
        db = DoubleBuffer(make_payload(0))
        barrier = threading.Barrier(9)
        seen = []
        lock = threading.Lock()

        def reader():
            barrier.wait()
            with db.acquire() as g:
                v = check_consistent(g.data)
                with lock:
                    seen.append(g.data["version"])

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        barrier.wait()
        db.publish(make_payload(1))
        for t in threads:
            t.join()
        self.assertEqual(len(seen), 8)
        for v in seen:
            self.assertIn(v, (0, 1))  # 只能是完整的旧版或新版


class TestReclamationTiming(unittest.TestCase):
    def test_no_reclaim_while_reading(self):
        """核心断言：读期间旧版本绝不被回收；释放后立即回收。"""
        reclaimed_ids = []
        db = DoubleBuffer(make_payload(0),
                          on_reclaim=lambda s: reclaimed_ids.append(s.version_id))

        guard_old = db.acquire()          # 钉住版本 0
        db.publish(make_payload(1))       # 版本 0 退休
        gc.collect()
        # 断言 1：有活跃读者，版本 0 不得被回收
        self.assertNotIn(0, reclaimed_ids)
        self.assertEqual(db.stats()["pending_retired"], 1)
        # 且数据仍然完整可读
        self.assertEqual(check_consistent(guard_old.data), 200)

        db.publish(make_payload(2))       # 版本 1 退休，无人引用 -> 立即回收
        self.assertIn(1, reclaimed_ids)
        self.assertNotIn(0, reclaimed_ids)  # 版本 0 仍被钉住

        guard_old.release()               # 释放最后一个引用
        # 断言 2：引用归零且已退休 -> 立即回收
        self.assertIn(0, reclaimed_ids)
        self.assertEqual(db.stats()["pending_retired"], 0)

    def test_reclaim_waits_for_slowest_reader(self):
        db = DoubleBuffer(make_payload(0))
        g1, g2 = db.acquire(), db.acquire()
        db.publish(make_payload(1))
        g1.release()
        self.assertEqual(db.stats()["pending_retired"], 1)  # g2 仍持有
        g2.release()
        self.assertEqual(db.stats()["pending_retired"], 0)

    def test_guard_use_after_release_raises(self):
        db = DoubleBuffer({})
        g = db.acquire()
        g.release()
        with self.assertRaises(RuntimeError):
            _ = g.data


class TestMemoryAndLatency(unittest.TestCase):
    def test_memory_peak_under_rapid_publish(self):
        """连续快速发布 + 并发读：tracemalloc 测内存峰值，统计回收延迟。"""
        gc.collect()
        db = DoubleBuffer(make_payload(0, n_keys=500))
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                with db.acquire() as g:
                    check_consistent(g.data)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()

        tracemalloc.start()
        t0 = time.perf_counter()
        n = 3000
        for i in range(1, n + 1):
            db.publish(make_payload(i, n_keys=500))
        wall = time.perf_counter() - t0
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        stop.set()
        for t in readers:
            t.join()

        s = db.stats()
        delays = [r.reclaim_delay for r in db.reclaimed]
        print("\n===== 内存与回收数据（连续快速发布）=====")
        print(f"发布次数            : {n}")
        print(f"发布总耗时          : {wall*1000:.1f} ms ({n/wall:.0f} 次/秒)")
        print(f"tracemalloc 峰值    : {peak/1024/1024:.2f} MiB")
        print(f"退休未回收峰值      : {s['peak_pending_retired']} 个版本")
        print(f"结束时退休未回收    : {s['pending_retired']} 个版本")
        print(f"已回收版本数        : {s['reclaimed_count']}")
        if delays:
            print(f"回收延迟 p50/p99/max: "
                  f"{statistics.median(delays)*1e6:.0f} / "
                  f"{sorted(delays)[int(len(delays)*0.99)]*1e6:.0f} / "
                  f"{max(delays)*1e6:.0f} µs")
        # 内存有界性断言：退休堆积有硬上界（读者数 + 少量在途）
        self.assertLessEqual(s["peak_pending_retired"], 4 + 2)
        self.assertEqual(s["pending_retired"], 0)

    def test_read_latency_unaffected_by_publish(self):
        """发布前后/发布期间读延迟对比：不得明显恶化。"""
        db = DoubleBuffer(make_payload(0))

        def measure(duration_s):
            samples = []
            deadline = time.perf_counter() + duration_s
            while time.perf_counter() < deadline:
                t0 = time.perf_counter()
                with db.acquire() as g:
                    check_consistent(g.data)
                samples.append(time.perf_counter() - t0)
            return samples

        def pct(samples, p):
            return sorted(samples)[int(len(samples) * p)] * 1e6

        before = measure(0.5)

        stop = threading.Event()
        samples_during = []
        done = threading.Event()

        def reader():
            while not stop.is_set():
                t0 = time.perf_counter()
                with db.acquire() as g:
                    check_consistent(g.data)
                samples_during.append(time.perf_counter() - t0)
            done.set()

        rt = threading.Thread(target=reader)
        rt.start()
        pub_t0 = time.perf_counter()
        for i in range(1, 2001):
            db.publish(make_payload(i))
        pub_wall = time.perf_counter() - pub_t0
        stop.set()
        rt.join()

        after = measure(0.5)

        print("\n===== 读延迟数据（发布前 / 发布期间 / 发布后）=====")
        print(f"样本数        : {len(before)} / {len(samples_during)} / {len(after)}")
        print(f"p50  (µs)     : {pct(before,.5):.1f} / {pct(samples_during,.5):.1f} / {pct(after,.5):.1f}")
        print(f"p99  (µs)     : {pct(before,.99):.1f} / {pct(samples_during,.99):.1f} / {pct(after,.99):.1f}")
        print(f"max  (µs)     : {max(before)*1e6:.1f} / {max(samples_during)*1e6:.1f} / {max(after)*1e6:.1f}")
        print(f"发布吞吐      : {2000/pub_wall:.0f} 次/秒（发布期间读未被阻塞）")
        # 发布期间 p50 不得比发布前恶化超过 10 倍（实际通常 < 2 倍）
        self.assertLess(pct(samples_during, .5), pct(before, .5) * 10 + 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
