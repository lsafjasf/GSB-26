"""refcount 库自测：生命周期、环检测、弱引用、并发不变量、性能与泄漏。"""

import threading
import time
import unittest

from refcount import DestroyedError, ObjectStore


class TestLifecycle(unittest.TestCase):
    def test_single_object(self):
        store = ObjectStore()
        events = []
        ref = store.create("hello", on_destroy=lambda i, v: events.append((i, v)))
        self.assertEqual(store.strong_count(ref), 1)
        self.assertEqual(ref.get(), "hello")
        self.assertFalse(store.is_destroyed(ref))
        ref.release()
        self.assertEqual(events, [(ref.id, "hello")])   # 回调恰好一次
        self.assertTrue(store.is_destroyed(ref.id))
        self.assertEqual(store.live_count(), 0)          # 无泄漏
        with self.assertRaises(DestroyedError):
            ref.get()

    def test_clone_counts(self):
        store = ObjectStore()
        ref = store.create(1)
        c1, c2 = ref.clone(), ref.clone()
        self.assertEqual(store.strong_count(ref), 3)
        c1.release()
        c2.release()
        self.assertEqual(store.strong_count(ref), 1)
        self.assertFalse(store.is_destroyed(ref))
        ref.release()
        self.assertEqual(store.live_count(), 0)

    def test_release_idempotent(self):
        store = ObjectStore()
        ref = store.create(1)
        ref.release()
        ref.release()  # 不抛错、计数不为负
        self.assertEqual(store.live_count(), 0)

    def test_long_chain(self):
        """10000 节点长链：释放头节点级联销毁全部（迭代实现，不爆栈）。"""
        store = ObjectStore()
        n = 10_000
        destroyed = []
        refs = [store.create(i, on_destroy=lambda i, v: destroyed.append(v))
                for i in range(n)]
        for i in range(n - 1):
            refs[i].set_field("next", refs[i + 1])
        for r in refs[1:]:
            r.release()  # 释放外部引用，仅靠链边存活
        self.assertEqual(store.live_count(), n)
        refs[0].release()
        self.assertEqual(len(destroyed), n)
        self.assertEqual(destroyed, list(range(n)))  # 父先于子
        self.assertEqual(store.live_count(), 0)

    def test_callback_order_and_weak_invalidated_first(self):
        """回调执行时弱引用必须已失效；父回调先于子回调。"""
        store = ObjectStore()
        order = []
        weak_seen_alive = []
        parent = store.create("p")
        child = store.create("c")
        parent.set_field("kid", child)
        child.release()
        wk_parent = store.weak(parent)
        wk_child = store.weak(child)
        parent.on_destroy(lambda i, v: (order.append("p"),
                                        weak_seen_alive.append(wk_parent.alive())))
        child.on_destroy(lambda i, v: (order.append("c"),
                                       weak_seen_alive.append(wk_child.alive())))
        parent.release()
        self.assertEqual(order, ["p", "c"])
        self.assertEqual(weak_seen_alive, [False, False])  # 回调时弱引用已失效
        wk_parent.release()
        wk_child.release()
        self.assertEqual(store.live_count(), 0)


class TestWeakRef(unittest.TestCase):
    def test_alive_then_dead(self):
        store = ObjectStore()
        ref = store.create("x")
        wk = store.weak(ref)
        self.assertTrue(wk.alive())
        upgraded = wk.get()
        self.assertEqual(upgraded.get(), "x")
        upgraded.release()
        ref.release()
        self.assertFalse(wk.alive())
        self.assertIsNone(wk.get())  # 已销毁：返回 None，绝不返回死对象
        wk.release()
        self.assertEqual(store.live_count(), 0)

    def test_weak_does_not_prevent_destroy(self):
        store = ObjectStore()
        ref = store.create(1)
        wk = store.weak(ref)
        self.assertEqual(store.weak_count(ref), 1)
        ref.release()
        self.assertTrue(wk._handle.destroyed)
        self.assertEqual(store.live_count(), 1)  # 句柄因弱引用仍在
        wk.release()
        self.assertEqual(store.live_count(), 0)  # 弱引用释放后句柄回收

    def test_weak_upgrade_keeps_alive(self):
        store = ObjectStore()
        ref = store.create("v")
        wk = store.weak(ref)
        upgraded = wk.get()
        ref.release()
        self.assertTrue(wk.alive())  # 升级得到的强引用维持对象存活
        self.assertEqual(upgraded.get(), "v")
        upgraded.release()
        self.assertFalse(wk.alive())
        wk.release()


class TestCycles(unittest.TestCase):
    def test_self_reference(self):
        store = ObjectStore()
        destroyed = []
        ref = store.create("self", on_destroy=lambda i, v: destroyed.append(i))
        ref.set_field("me", ref)  # 自引用
        self.assertEqual(store.strong_count(ref), 2)
        ref.release()
        self.assertEqual(destroyed, [])          # 计数不归零，单靠计数无法回收
        self.assertEqual(store.unreachable(), [ref.id])  # 环检测报告
        self.assertEqual(store.collect(), [ref.id])
        self.assertEqual(destroyed, [ref.id])
        self.assertEqual(store.live_count(), 0)

    def test_three_node_cycle_reported(self):
        store = ObjectStore()
        refs = [store.create(chr(ord('a') + i)) for i in range(3)]
        for i in range(3):
            refs[i].set_field("next", refs[(i + 1) % 3])
        ids = sorted(r.id for r in refs)
        wk = store.weak(refs[0])
        for r in refs:
            r.release()
        garbage = store.unreachable()
        self.assertEqual(garbage, ids)           # 报告环上全部对象
        self.assertTrue(wk.alive())              # 回收前仍存活
        collected = store.collect()
        self.assertEqual(sorted(collected), ids)
        self.assertFalse(wk.alive())             # 回收后弱引用失效
        self.assertEqual(store.unreachable(), [])
        wk.release()
        self.assertEqual(store.live_count(), 0)

    def test_cycle_with_live_root_not_collected(self):
        """环被外部根引用时不可回收。"""
        store = ObjectStore()
        a = store.create("a")
        b = store.create("b")
        a.set_field("b", b)
        b.set_field("a", a)
        b.release()  # b 仅被 a 引用，但 a 有外部根
        self.assertEqual(store.unreachable(), [])
        self.assertEqual(store.collect(), [])
        a.release()  # 整个环失去根，变为垃圾
        self.assertEqual(sorted(store.collect()), [a.id, b.id])
        self.assertEqual(store.live_count(), 0)

    def test_garbage_chain_off_cycle(self):
        """环外挂着只被环引用的对象，一并判定为可回收。"""
        store = ObjectStore()
        a, b = store.create("a"), store.create("b")
        tail = store.create("tail")
        a.set_field("b", b)
        b.set_field("a", a)
        a.set_field("tail", tail)
        tail.release()
        for r in (a, b):
            r.release()
        self.assertEqual(sorted(store.collect()), [a.id, b.id, tail.id])
        self.assertEqual(store.live_count(), 0)


class TestConcurrency(unittest.TestCase):
    THREADS = 8
    OPS = 20_000

    def test_concurrent_inc_dec_no_drift_no_early_destroy(self):
        """不变量：持有主引用期间对象绝不销毁；最终计数精确等于 1。"""
        store = ObjectStore()
        destroyed = []
        ref = store.create("shared",
                           on_destroy=lambda i, v: destroyed.append(i))
        errors = []

        def worker():
            try:
                for _ in range(TestConcurrency.OPS):
                    clone = ref.clone()          # 若提前销毁会抛 DestroyedError
                    if store.is_destroyed(ref):  # 不变量：持引用期间不得销毁
                        errors.append("destroyed while refs alive")
                        return
                    clone.release()
            except Exception as exc:             # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker)
                   for _ in range(TestConcurrency.THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(destroyed, [])                  # 未提前销毁
        self.assertEqual(store.strong_count(ref), 1)     # 计数不漂移
        ref.release()
        self.assertEqual(destroyed, [ref.id])            # 回调恰好一次
        self.assertEqual(store.live_count(), 0)

    def test_concurrent_create_destroy(self):
        """并发创建/链接/销毁：回调总数 == 创建总数，注册表清零。"""
        store = ObjectStore()
        created = [0] * TestConcurrency.THREADS
        destroyed = []
        dlock = threading.Lock()
        errors = []

        def worker(tid):
            try:
                for k in range(2_000):
                    made = []
                    def cb(i, v, made=made):
                        with dlock:
                            destroyed.append(i)
                    a = store.create((tid, k), on_destroy=cb)
                    b = store.create((tid, k), on_destroy=cb)
                    a.set_field("peer", b)
                    b.set_field("peer", a)   # 二元环
                    created[tid] += 2
                    a.release()
                    b.release()
                    made.append((a, b))
                    store.collect()          # 并发回收
            except Exception as exc:         # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in range(TestConcurrency.THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        store.collect()
        total_created = sum(created)
        self.assertEqual(len(destroyed), total_created)   # 每个对象恰好销毁一次
        self.assertEqual(len(set(destroyed)), total_created)
        self.assertEqual(store.live_count(), 0)           # 无泄漏

    def test_concurrent_weak_upgrade_never_returns_dead(self):
        """弱引用并发升级：要么得到可用强引用，要么 None，绝不得到死对象。"""
        store = ObjectStore()
        errors = []
        rounds = 500

        def hammer(wk, stop):
            try:
                while not stop.is_set():
                    upgraded = wk.get()
                    if upgraded is not None:
                        upgraded.get()       # 死对象会在这里抛 DestroyedError
                        upgraded.release()
            except Exception as exc:         # noqa: BLE001
                errors.append(repr(exc))

        for _ in range(rounds):
            ref = store.create("v")
            wk = store.weak(ref)
            stop = threading.Event()
            ts = [threading.Thread(target=hammer, args=(wk, stop))
                  for _ in range(4)]
            for t in ts:
                t.start()
            ref.release()                    # 与升级并发销毁
            stop.set()
            for t in ts:
                t.join()
            self.assertFalse(wk.alive())
            self.assertIsNone(wk.get())
            wk.release()
        self.assertEqual(errors, [])
        self.assertEqual(store.live_count(), 0)


class TestPerfAndLeak(unittest.TestCase):
    def test_million_inc_dec(self):
        """百万次增减引用：计时 + 计数/泄漏断言。"""
        store = ObjectStore()
        ref = store.create("bench")
        n = 1_000_000
        start = time.perf_counter()
        for _ in range(n):
            ref.clone().release()
        elapsed = time.perf_counter() - start
        print(f"\n[perf] {n:,} 次 acquire/release 耗时 {elapsed:.3f}s "
              f"({n / elapsed / 1e6:.2f} M ops/s)")
        self.assertEqual(store.strong_count(ref), 1)   # 计数不漂移
        self.assertFalse(store.is_destroyed(ref))      # 未提前销毁
        ref.release()
        self.assertEqual(store.live_count(), 0)        # 无泄漏

    def test_million_weak_upgrades(self):
        store = ObjectStore()
        ref = store.create("bench")
        wk = store.weak(ref)
        n = 1_000_000
        start = time.perf_counter()
        for _ in range(n):
            upgraded = wk.get()
            assert upgraded is not None
            upgraded.release()
        elapsed = time.perf_counter() - start
        print(f"[perf] {n:,} 次弱引用升级/释放 耗时 {elapsed:.3f}s "
              f"({n / elapsed / 1e6:.2f} M ops/s)")
        self.assertEqual(store.weak_count(ref), 1)
        ref.release()
        wk.release()
        self.assertEqual(store.live_count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
