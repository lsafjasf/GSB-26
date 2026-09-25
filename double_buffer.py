"""双缓冲数据切换库（仅标准库）。

模型：原子指针切换 + 引用计数回收（RCU 风格的精简版）。

- publish(data): 在锁外完整构建新版本，仅在临界区内原子切换当前指针；
  正在进行的读持有旧版本引用，完全不被阻塞，且永远看到完整一致的版本。
- acquire(): 返回一个读守卫（Guard）。守卫持有期间，该版本保证不被回收。
  守卫是上下文管理器，推荐 with 语法；也支持显式 release()。
- 回收时机（引用计数法）：版本被「退休」（被更新的版本替换）且引用计数
  降为 0 的那一刻立即回收。因此：
    * 读期间绝不回收（引用 >= 1）；
    * 无读者时回收是即时的，退休版本不会堆积，内存占用有界
      （上界 = 当前版本 + 被活跃读者钉住的旧版本）。
"""

import threading
import time

__all__ = ["DoubleBuffer", "Guard", "VersionStats"]


class VersionStats:
    """单次回收事件的记录，用于观测回收时机与延迟。"""

    __slots__ = ("version_id", "published_at", "retired_at", "reclaimed_at")

    def __init__(self, version_id, published_at, retired_at, reclaimed_at):
        self.version_id = version_id
        self.published_at = published_at      # 版本发布时刻
        self.retired_at = retired_at          # 被替换（退休）时刻
        self.reclaimed_at = reclaimed_at      # 实际回收时刻

    @property
    def reclaim_delay(self):
        """从退休到回收的延迟（秒）。无读者钉住时约等于 0。"""
        return self.reclaimed_at - self.retired_at


class _Version:
    __slots__ = ("version_id", "data", "refs", "retired", "published_at", "retired_at")

    def __init__(self, version_id, data):
        self.version_id = version_id
        self.data = data
        self.refs = 0            # 活跃读者数
        self.retired = False     # 是否已被更新的版本替换
        self.published_at = time.perf_counter()
        self.retired_at = None


class Guard:
    """读守卫：持有期间保证版本不被回收。"""

    __slots__ = ("_buffer", "_version", "_released")

    def __init__(self, buffer, version):
        self._buffer = buffer
        self._version = version
        self._released = False

    @property
    def data(self):
        if self._released:
            raise RuntimeError("guard already released")
        return self._version.data

    @property
    def version_id(self):
        return self._version.version_id

    def release(self):
        if not self._released:
            self._released = True
            self._buffer._release(self._version)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def __del__(self):  # 兜底，防止忘记 release
        try:
            self.release()
        except Exception:
            pass


class DoubleBuffer:
    """双缓冲（实为多缓冲）整体替换容器。

    线程安全：publish 与 acquire 可被任意多线程并发调用。
    """

    def __init__(self, initial=None, on_reclaim=None):
        """
        initial: 初始数据（版本 0）。
        on_reclaim: 可选回调 on_reclaim(VersionStats)，在每次回收时调用，
                    用于测试/监控回收时机。回调在锁外触发。
        """
        self._lock = threading.Lock()
        self._current = _Version(0, initial)
        self._next_id = 1
        self._on_reclaim = on_reclaim
        # 观测指标
        self._reclaimed = []          # List[VersionStats]
        self._pending_retired = 0     # 已退休但尚未回收的版本数
        self._peak_pending_retired = 0
        self._publish_count = 0

    # ---- 写路径 ----

    def publish(self, data):
        """发布新版本。不阻塞正在进行的读；返回新版本号。"""
        new_version = _Version(self._next_id, data)  # 锁外构建
        to_reclaim = []
        with self._lock:
            self._next_id += 1
            self._publish_count += 1
            old = self._current
            self._current = new_version          # 原子切换
            old.retired = True
            old.retired_at = time.perf_counter()
            self._pending_retired += 1
            self._maybe_reclaim_locked(old, to_reclaim)
            self._peak_pending_retired = max(self._peak_pending_retired,
                                             self._pending_retired)
        self._fire_reclaims(to_reclaim)
        return new_version.version_id

    # ---- 读路径 ----

    def acquire(self):
        """获取当前版本的读守卫。临界区仅为一次计数自增，O(1)。"""
        with self._lock:
            version = self._current
            version.refs += 1
        return Guard(self, version)

    # ---- 内部 ----

    def _release(self, version):
        to_reclaim = []
        with self._lock:
            version.refs -= 1
            if version.retired:
                self._maybe_reclaim_locked(version, to_reclaim)
        self._fire_reclaims(to_reclaim)

    def _maybe_reclaim_locked(self, version, out):
        """锁内调用：退休且无人引用 -> 立即回收。"""
        if version.retired and version.refs == 0:
            self._pending_retired -= 1
            version.data = None  # 释放数据引用，交还内存
            out.append(VersionStats(version.version_id,
                                    version.published_at,
                                    version.retired_at,
                                    time.perf_counter()))

    def _fire_reclaims(self, stats_list):
        for stats in stats_list:
            self._reclaimed.append(stats)
            if self._on_reclaim is not None:
                self._on_reclaim(stats)

    # ---- 观测 ----

    def stats(self):
        """返回当前观测指标快照（字典）。"""
        with self._lock:
            return {
                "current_version": self._current.version_id,
                "current_refs": self._current.refs,
                "publish_count": self._publish_count,
                "pending_retired": self._pending_retired,
                "peak_pending_retired": self._peak_pending_retired,
                "reclaimed_count": len(self._reclaimed),
            }

    @property
    def reclaimed(self):
        """已回收版本的 VersionStats 列表（按回收顺序）。"""
        return list(self._reclaimed)
