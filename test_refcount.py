"""test_refcount.py — 单元测试 + 并发不变量测试。运行: python3 test_refcount.py -v"""

import threading
import unittest

from refcount import (
    Managed, WeakRef, ObjectDestroyedError,
    live_count, find_cycles, collect, unreachable, auto_roots,
)


class TestSingleObject(unittest.TestCase):
    def test_count_and_release(self):
        base = live_count()
        obj = Managed("x", name="single")
        self.assertEqual(obj.strong_count, 1)
        obj.retain()
        obj.retain()
        self.assertEqual(obj.strong_count, 3)
        obj.release()
        self.assertEqual(obj.strong_count, 2)
        obj.release()
        obj.release()
        self.assertFalse(obj.alive)
        self.assertEqual(obj.strong_count, 0)
        self.assertEqual(live_count(), base)  # 无泄漏

    def test_destroy_callback_order(self):
        order = []
        parent = Managed(name="parent")
        child = Managed(name="child")
        parent.add_child(child)
        parent.on_destroy(lambda o: order.append(("parent_cb1", o.name)))
        parent.on_destroy(lambda o: order.append(("parent_cb2", o.name)))
        child.on_destroy(lambda o: order.append(("child_cb", o.name)))
        child.release()   # 释放创建者引用，仍被 parent 持有
        self.assertTrue(child.alive)
        parent.release()  # 级联销毁
        self.assertEqual(order, [
            ("parent_cb1", "parent"),
            ("parent_cb2", "parent"),
            ("child_cb", "child"),
        ])

    def test_double_release_raises(self):
        obj = Managed()
        obj.release()
        with self.assertRaises(RuntimeError):
            obj.release()

    def test_value_after_destroy_raises(self):
        obj = Managed("v")
        obj.release()
        with self.assertRaises(ObjectDestroyedError):
            _ = obj.value


class TestLongChain(unittest.TestCase):
    def test_chain_cascade_destroy(self):
        n = 10_000  # 长链，验证迭代销毁不爆递归栈
        base = live_count()
        head = Managed(name="n0")
        node = head
        destroyed = []
        for i in range(1, n):
            child = Managed(name=f"n{i}")
            child.on_destroy(lambda o, i=i: destroyed.append(i))
            node.add_child(child)
            child.release()  # 创建者放手，由父节点持有
            node = child
        self.assertEqual(live_count(), base + n)
        head.release()
        self.assertEqual(len(destroyed), n - 1)
        self.assertEqual(live_count(), base)  # 全部级联销毁，无泄漏


class TestSelfReference(unittest.TestCase):
    def test_self_cycle_detected_and_collected(self):
        base = live_count()
        obj = Managed(name="selfish")
        obj.add_child(obj)  # 自引用
        obj.release()       # 释放外部引用后仍被自己持有
        self.assertTrue(obj.alive)
        cycles = find_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertIs(cycles[0]["objects"][0], obj)
        self.assertTrue(cycles[0]["reclaimable"])
        collected = collect()
        self.assertIn(obj, collected)
        self.assertFalse(obj.alive)
        self.assertEqual(live_count(), base)


class TestCycles(unittest.TestCase):
    def test_unreachable_cycle_collected(self):
        base = live_count()
        a, b, c = Managed(name="a"), Managed(name="b"), Managed(name="c")
        a.add_child(b); b.add_child(c); c.add_child(a)
        for o in (a, b, c):
            o.release()  # 放弃外部引用，只剩环内互引
        self.assertTrue(a.alive and b.alive and c.alive)  # 纯计数无法回收
        cycles = find_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual({id(o) for o in cycles[0]["objects"]}, {id(a), id(b), id(c)})
        self.assertTrue(cycles[0]["reclaimable"])
        collected = collect()
        self.assertEqual(len(collected), 3)
        self.assertEqual(live_count(), base)

    def test_rooted_cycle_reported_not_reclaimable(self):
        """确实不可回收的环：仍被外部根持有，报告涉及对象且不回收。"""
        a, b = Managed(name="ra"), Managed(name="rb")
        a.add_child(b); b.add_child(a)
        b.release()  # b 只被 a 持有；a 仍有外部引用 => 环不可回收
        cycles = find_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual({o.name for o in cycles[0]["objects"]}, {"ra", "rb"})
        self.assertFalse(cycles[0]["reclaimable"])  # 报告：不可回收
        self.assertEqual(collect(), [])             # 不回收可达对象
        self.assertTrue(a.alive and b.alive)
        a.release()  # 现在整体不可达
        self.assertEqual(len(collect()), 2)

    def test_unreachable_reports_objects(self):
        x, y = Managed(name="x"), Managed(name="y")
        x.add_child(y); y.add_child(x)
        x.release(); y.release()
        names = {o.name for o in unreachable()}
        self.assertEqual(names, {"x", "y"})
        collect()

    def test_weak_count_not_a_root(self):
        obj = Managed()
        w = WeakRef(obj)
        obj.add_child(obj)
        obj.release()
        self.assertEqual(auto_roots(), [])  # 弱引用不构成根
        collect()
        self.assertFalse(w.alive)


class TestWeakRef(unittest.TestCase):
    def test_weak_ref_invalidation(self):
        obj = Managed("data")
        w = WeakRef(obj)
        self.assertTrue(w.alive)
        self.assertIs(w.get(), obj)          # 对象仍在：可区分
        self.assertEqual(obj.weak_count, 1)
        obj.release()
        self.assertFalse(w.alive)            # 对象已释放：可区分
        self.assertIsNone(w.get())           # 不返回已销毁对象
        self.assertEqual(obj.weak_count, 1)  # 弱引用本身仍可查询

    def test_weak_ref_does_not_keep_alive(self):
        base = live_count()
        obj = Managed()
        w = WeakRef(obj)
        obj.release()
        self.assertIsNone(w.get())
        self.assertEqual(live_count(), base)

    def test_weak_ref_to_cycle_after_collect(self):
        a, b = Managed(), Managed()
        a.add_child(b); b.add_child(a)
        wa, wb = WeakRef(a), WeakRef(b)
        a.release(); b.release()
        collect()
        self.assertIsNone(wa.get())
        self.assertIsNone(wb.get())

    def test_weak_ref_close(self):
        obj = Managed()
        w = WeakRef(obj)
        w.close()
        self.assertEqual(obj.weak_count, 0)
        obj.release()


class TestConcurrency(unittest.TestCase):
    THREADS = 8
    OPS = 5_000

    def test_concurrent_retain_release_no_drift(self):
        """多线程并发增减引用：计数不漂移，对象不提前销毁。"""
        obj = Managed()
        errors = []

        def worker():
            try:
                for _ in range(self.OPS):
                    obj.retain()
                    # 不变量 I2：持有引用期间对象必须存活
                    if not obj.alive:
                        errors.append("destroyed while referenced")
                    # 不变量 I1：计数永不为负
                    if obj.strong_count < 1:
                        errors.append(f"bad count {obj.strong_count}")
                    obj.release()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(self.THREADS)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(errors, [])
        self.assertEqual(obj.strong_count, 1)   # 计数回到初始值：不漂移
        self.assertTrue(obj.alive)
        obj.release()
        self.assertFalse(obj.alive)

    def test_concurrent_create_destroy(self):
        """并发创建/销毁对象：无泄漏、无异常、存活数守恒。"""
        base = live_count()
        errors = []
        per_thread = 2_000

        def worker():
            try:
                for _ in range(per_thread):
                    o = Managed()
                    w = WeakRef(o)
                    o.retain()
                    o.release()
                    if w.get() is None:
                        errors.append("destroyed while strong ref held")
                    o.release()
                    if w.get() is not None:
                        errors.append("weak ref returned destroyed object")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(self.THREADS)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(errors, [])
        self.assertEqual(live_count(), base)  # 全部回收，无泄漏

    def test_concurrent_children_and_collect(self):
        """并发建图 + 周期性 collect：存活对象绝不被误回收。"""
        base = live_count()
        root = Managed(name="root")
        errors = []
        stop = threading.Event()

        def builder():
            try:
                while not stop.is_set():
                    child = Managed()
                    root.add_child(child)
                    root.remove_child(child)
                    child.release()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        def collector():
            while not stop.is_set():
                collect()
                if not root.alive:
                    errors.append("reachable root collected")

        builders = [threading.Thread(target=builder) for _ in range(4)]
        coll = threading.Thread(target=collector)
        for t in builders: t.start()
        coll.start()
        import time; time.sleep(0.5)
        stop.set()
        for t in builders: t.join()
        coll.join()

        self.assertEqual(errors, [])
        self.assertTrue(root.alive)
        root.release()
        collect()
        self.assertEqual(live_count(), base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
