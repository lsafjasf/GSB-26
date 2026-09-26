"""基准：百万次增减引用 / 弱引用升级耗时 + 泄漏检查。"""

import gc
import sys
import time

from refcount import ObjectStore


def main():
    n = 1_000_000
    store = ObjectStore()

    ref = store.create("bench")
    t0 = time.perf_counter()
    for _ in range(n):
        ref.clone().release()
    t1 = time.perf_counter()
    print(f"强引用 acquire/release x {n:,}: {t1 - t0:.3f}s "
          f"({n / (t1 - t0) / 1e6:.2f} M ops/s)")
    assert store.strong_count(ref) == 1, "计数漂移!"
    assert not store.is_destroyed(ref), "提前销毁!"

    wk = store.weak(ref)
    t0 = time.perf_counter()
    for _ in range(n):
        upgraded = wk.get()
        assert upgraded is not None
        upgraded.release()
    t1 = time.perf_counter()
    print(f"弱引用 upgrade/release  x {n:,}: {t1 - t0:.3f}s "
          f"({n / (t1 - t0) / 1e6:.2f} M ops/s)")

    # 百万对象创建/销毁泄漏检查
    t0 = time.perf_counter()
    destroyed = [0]
    for i in range(n):
        r = store.create(i, on_destroy=lambda _i, _v: destroyed.__setitem__(0, destroyed[0] + 1))
        r.release()
    t1 = time.perf_counter()
    print(f"创建+销毁对象        x {n:,}: {t1 - t0:.3f}s "
          f"({n / (t1 - t0) / 1e6:.2f} M ops/s)")
    assert destroyed[0] == n, f"回调次数 {destroyed[0]} != {n}"

    ref.release()
    wk.release()
    gc.collect()
    print(f"泄漏检查: live handles = {store.live_count()} "
          f"(期望 0), 销毁回调 = {destroyed[0]:,}/{n:,}")
    assert store.live_count() == 0, "句柄泄漏!"
    print("OK: 无计数漂移、无提前销毁、无泄漏")


if __name__ == "__main__":
    sys.exit(main())
