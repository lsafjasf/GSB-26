"""double_buffer 自测：功能、读一致性、回收时机断言、并发。

运行：python3 test_double_buffer.py -v
"""

import threading
import time
import unittest

from double_buffer import DoubleBuffer


def make_version(gen, size=64):
    """构造内部自洽的版本数据：payload 全部等于 gen。"""
    return {"id": gen, "payload": bytes([gen % 256]) * size}


def check_consistent(data):
    return data["id"] % 256 == data["payload"][0] and all(
        b == data["payload"][0] for b in data["payload"]
    )


class TestBasicPublish(unittest.TestCase):
    def test_single_publish(self):
        buf = DoubleBuffer(initial=make_version(0))
        self.assertEqual(buf.read()["id"], 0)
        gen = buf.publish(make_version(1))
        self.assertEqual(gen, 1)
        self.assertEqual(buf.read()["id"], 1)
        self.assertEqual(buf.current_generation, 1)

    def test_sequential_publishes(self):
        buf = DoubleBuffer()
        for i in range(1, 1001):
            buf.publish(make_version(i))
            self.assertEqual(buf.read()["id"], i)
        self.assertEqual(buf.current_generation, 1000)
        # 无读者时退休版本应立即回收，存活版本恒为 1
        self.assertEqual(buf.live_versions, 1)
        self.assertEqual(buf.reclaimed_count, 1000)

    def test_publish_empty_data(self):
        buf = DoubleBuffer(initial=make_version(0))
        for empty in (b"", "", {}, [], (), 0, None):
            buf.publish(empty)
            self.assertEqual(buf.read(), empty)
        self.assertEqual(buf.live_versions, 1)

    def test_snapshot_isolation(self):
        """发布新版本后，旧快照仍看到旧数据（读不被写影响）。"""
        buf = DoubleBuffer(initial=make_version(0))
        snap = buf.acquire()
        buf.publish(make_version(1))
        self.assertEqual(snap.data["id"], 0)   # 旧快照不变
        self.assertEqual(buf.read()["id"], 1)  # 新读看到新版本
        snap.release()
        with self.assertRaises(RuntimeError):
            _ = snap.data


class TestReclamationTiming(unittest.TestCase):
    def test_not_reclaimed_while_reading(self):
        """核心断言：读期间版本绝不回收，释放后立即回收。"""
        reclaimed = []
        buf = DoubleBuffer(initial=make_version(0),
                           on_reclaim=lambda d: reclaimed.append(d["id"]))
        snap = buf.acquire()                    # 读者持有 v0
        buf.publish(make_version(1))            # v0 退休
        # 断言1：有读者引用，不得回收
        self.assertEqual(reclaimed, [])
        self.assertEqual(buf.reclaimed_count, 0)
        self.assertEqual(buf.live_versions, 2)
        # 断言2：读期间数据完整可用
        self.assertTrue(check_consistent(snap.data))
        snap.release()
        # 断言3：引用归零后立即回收
        self.assertEqual(reclaimed, [0])
        self.assertEqual(buf.reclaimed_count, 1)
        self.assertEqual(buf.live_versions, 1)

    def test_reclaim_waits_for_last_reader(self):
        """多个读者持有同一退休版本，最后一个释放后才回收。"""
        reclaimed = []
        buf = DoubleBuffer(initial=make_version(0),
                           on_reclaim=lambda d: reclaimed.append(d["id"]))
        snaps = [buf.acquire() for _ in range(5)]
        buf.publish(make_version(1))
        for s in snaps[:4]:
            s.release()
            self.assertEqual(reclaimed, [])     # 仍有读者，不回收
        snaps[4].release()
        self.assertEqual(reclaimed, [0])        # 最后一个释放才回收

    def test_rapid_publish_memory_bounded(self):
        """连续快速发布：无读者时存活版本 <= 2，内存不随发布数增长。"""
        buf = DoubleBuffer()
        peak = 0
        for i in range(1, 20001):
            buf.publish(make_version(i, size=1024))
            peak = max(peak, buf.live_versions)
        self.assertLessEqual(peak, 2)
        self.assertEqual(buf.live_versions, 1)
        self.assertEqual(buf.reclaimed_count, 20000)

    def test_slow_reader_pins_exactly_one_extra(self):
        """一个慢读者只钉住它读的那个版本，后续版本照常回收。"""
        buf = DoubleBuffer(initial=make_version(0))
        snap = buf.acquire()                    # 钉住 v0
        for i in range(1, 101):
            buf.publish(make_version(i))
            self.assertLessEqual(buf.live_versions, 2)
        self.assertEqual(buf.reclaimed_count, 99)   # v1..v99 已回收，v100 是当前版本
        snap.release()
        self.assertEqual(buf.reclaimed_count, 100)  # v0 最后回收
        self.assertEqual(buf.live_versions, 1)


class TestConcurrency(unittest.TestCase):
    def test_publish_and_read_start_together(self):
        """发布与读同时开始（barrier 同步起跑），读必须看到完整一致的版本。"""
        buf = DoubleBuffer(initial=make_version(0))
        n_readers, n_iters = 8, 2000
        barrier = threading.Barrier(n_readers + 1)
        stop = threading.Event()
        errors = []
        seen_gens = [set() for _ in range(n_readers)]

        def reader(idx):
            barrier.wait()
            for _ in range(n_iters):
                with buf.acquire() as snap:
                    data = snap.data
                    if not check_consistent(data):
                        errors.append(f"reader{idx} 看到撕裂数据: {data['id']}")
                    seen_gens[idx].add(data["id"])

        def publisher():
            barrier.wait()
            for i in range(1, 501):
                buf.publish(make_version(i))
            stop.set()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(n_readers)]
        threads.append(threading.Thread(target=publisher))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(buf.current_generation, 500)
        # 每个读者看到的版本号集合应是合法版本号的子集
        for s in seen_gens:
            self.assertTrue(all(0 <= g <= 500 for g in s))

    def test_readers_never_blocked_by_publish(self):
        """发布风暴期间读者始终能拿到一致快照（活性 + 一致性）。"""
        buf = DoubleBuffer(initial=make_version(0))
        stop = threading.Event()
        errors = []
        reads_done = [0]

        def reader():
            while not stop.is_set():
                with buf.acquire() as snap:
                    if not check_consistent(snap.data):
                        errors.append("torn read")
                reads_done[0] += 1

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        for i in range(1, 5001):                # 发布风暴
            buf.publish(make_version(i, size=256))
        stop.set()
        for t in readers:
            t.join(timeout=5)
            self.assertFalse(t.is_alive())      # 读者未被卡死
        self.assertEqual(errors, [])
        self.assertGreater(reads_done[0], 0)
        self.assertEqual(buf.live_versions, 1)  # 风暴后全部回收


if __name__ == "__main__":
    unittest.main(verbosity=2)
