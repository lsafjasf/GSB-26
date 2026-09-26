"""双缓冲数据切换库（仅标准库）。

整体替换配置/索引场景：
- 发布（publish）原子切换当前版本指针，不等待任何读者；
- 读（acquire/read）拿到一个 Snapshot 守卫，守卫存活期间该版本保证不被回收；
- 回收策略：引用计数 + 立即回收。版本被退休（retire）且引用计数归零的
  那一刻即被回收，回收回调在锁外执行，不会阻塞发布或读。

内存上界：存活版本数 = 1（当前版本）+ 仍被读者引用的退休版本数。
没有读者时，连续快速发布的内存峰值恒为 2 个版本。
"""

from __future__ import annotations

import threading

_UNSET = object()


class _Version:
    __slots__ = ("data", "refs", "retired", "reclaimed", "gen")

    def __init__(self, data, gen):
        self.data = data
        self.refs = 0          # 活跃读者数
        self.retired = False   # 是否已被新版本取代
        self.reclaimed = False
        self.gen = gen


class Snapshot:
    """读守卫。持有期间，对应版本不会被回收（断言测试依赖此保证）。"""

    __slots__ = ("_buf", "_version", "_released")

    def __init__(self, buf: "DoubleBuffer", version: _Version):
        self._buf = buf
        self._version = version
        self._released = False

    @property
    def data(self):
        if self._released:
            raise RuntimeError("snapshot already released")
        return self._version.data

    @property
    def generation(self) -> int:
        return self._version.gen

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._buf._release(self._version)

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class DoubleBuffer:
    def __init__(self, initial=_UNSET, on_reclaim=None):
        """
        initial:    初始版本数据（缺省为 None）。
        on_reclaim: 可选回调 fn(data)，在版本被回收时调用（锁外执行），
                    用于关闭文件、释放 mmap 等真正的资源清理。
        """
        self._lock = threading.Lock()
        self._gen = 0
        self._current = _Version(None if initial is _UNSET else initial, 0)
        self._retired_pending = 0   # 已退休但仍有读者、等待回收的版本数
        self._on_reclaim = on_reclaim
        self.reclaimed_count = 0    # 已回收版本总数（不含当前版本）

    # ---------------- 写路径 ----------------

    def publish(self, data) -> int:
        """原子发布新版本，返回版本号。绝不等待读者，读者也无需等待它。"""
        with self._lock:
            self._gen += 1
            old = self._current
            self._current = _Version(data, self._gen)
            old.retired = True
            self._retired_pending += 1
            self._maybe_reclaim_locked(old)
            return self._gen

    # ---------------- 读路径 ----------------

    def acquire(self) -> Snapshot:
        """获取当前版本的读守卫。临界区仅为一次指针读 + 计数自增。"""
        with self._lock:
            v = self._current
            v.refs += 1
        return Snapshot(self, v)

    def read(self, fn=None):
        """便捷接口：在守卫保护下读取数据（可对数据应用 fn）。"""
        with self.acquire() as snap:
            return fn(snap.data) if fn is not None else snap.data

    def _release(self, v: _Version) -> None:
        with self._lock:
            v.refs -= 1
            self._maybe_reclaim_locked(v)

    # ---------------- 回收 ----------------

    def _maybe_reclaim_locked(self, v: _Version) -> None:
        """回收时机：已退休 且 引用计数归零 且 尚未回收。调用方须持锁。"""
        if not (v.retired and v.refs == 0 and not v.reclaimed):
            return
        v.reclaimed = True
        self._retired_pending -= 1
        self.reclaimed_count += 1
        data, v.data = v.data, None   # 断引用，交给 GC
        cb = self._on_reclaim
        if cb is not None:
            self._lock.release()
            try:
                cb(data)              # 回调在锁外执行，不阻塞发布/读
            finally:
                self._lock.acquire()

    # ---------------- 观测（测试/基准用） ----------------

    @property
    def live_versions(self) -> int:
        """当前存活（未回收）的版本数：1 个当前版本 + 等待引用的退休版本。"""
        with self._lock:
            return 1 + self._retired_pending

    @property
    def current_generation(self) -> int:
        with self._lock:
            return self._current.gen
