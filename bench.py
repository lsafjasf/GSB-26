"""bench.py — 百万次增减引用性能 + 泄漏检查。运行: python3 bench.py"""

import gc
import threading
import time

from refcount import Managed, WeakRef, live_count, collect

N = 1_000_000


def bench_single_thread():
    obj = Managed()
    t0 = time.perf_counter()
    for _ in range(N):
        obj.retain()
        obj.release()
    dt = time.perf_counter() - t0
    assert obj.strong_count == 1 and obj.alive
    obj.release()
    return dt


def bench_multi_thread(threads=8):
    obj = Managed()
    per = N // threads

    def worker():
        for _ in range(per):
            obj.retain()
            obj.release()

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    t0 = time.perf_counter()
    for t in ts: t.start()
    for t in ts: t.join()
    dt = time.perf_counter() - t0
    assert obj.strong_count == 1 and obj.alive, f"count drift: {obj.strong_count}"
    obj.release()
    return dt


def bench_create_destroy():
    t0 = time.perf_counter()
    for _ in range(N):
        o = Managed()
        o.release()
    dt = time.perf_counter() - t0
    return dt


def leak_check():
    base = live_count()
    # 1) 普通对象批量创建销毁
    for _ in range(100_000):
        Managed().release()
    # 2) 批量环回收
    for _ in range(10_000):
        a, b = Managed(), Managed()
        a.add_child(b); b.add_child(a)
        a.release(); b.release()
        collected = collect()
        assert len(collected) == 2
    # 3) 弱引用批量失效
    for _ in range(100_000):
        o = Managed()
        w = WeakRef(o)
        o.release()
        assert w.get() is None
    gc.collect()
    return base, live_count()


if __name__ == "__main__":
    print(f"ops = {N:,}")
    dt = bench_single_thread()
    print(f"单线程 retain+release x{N:,}: {dt:.3f}s  ({N/dt/1e6:.2f} M ops/s)")
    dt = bench_multi_thread()
    print(f"8线程  retain+release x{N:,}: {dt:.3f}s  ({N/dt/1e6:.2f} M ops/s)  计数无漂移")
    base_before = live_count()
    dt = bench_create_destroy()
    print(f"单线程 create+destroy x{N:,}: {dt:.3f}s  ({N/dt/1e6:.2f} M ops/s)")
    assert live_count() == base_before, "leak after create/destroy bench"
    base, after = leak_check()
    status = "无泄漏" if after == base else f"泄漏 {after - base} 个对象!"
    print(f"泄漏检查: 基准存活 {base} -> 测试后存活 {after}  =>  {status}")
