"""请求批处理与合并库（仅依赖标准库）。

设计要点
--------
* 数量阈值与时间窗口先到先触发：窗口内累积到 ``max_batch_size`` 立即提交，
  否则窗口结束时提交。
* 同一键的并发请求合并：窗口内同键共享一个 Entry，提交只发一次；在途
  （已提交未返回）的同键请求同样合并，只等同一个在途结果。
* 每个调用方持有自己的 Future：单个子请求失败只影响对应调用方；
  整批失败 / 整批超时时该批所有等待方收到可区分的异常。
* 时间可注入：测试使用 ``VirtualClock``（确定性地推进虚拟时间），
  生产使用 ``RealScheduler``（线程 + 真实时间）。

下游 handler 协议
-----------------
``handler(keys, complete)`` 处理一批键（已按 max_batch_size 分片）。

* 同步返回 ``{key: value 或 Exception}``；值若为 ``Exception`` 实例表示该子请求失败。
* 或稍后调用 ``complete(result_dict)`` / ``complete(exception)`` 支持异步。
* handler 自身抛异常 / complete(Exception) 视为整批失败。
* 漏掉的键视为下游协议错误，按整批失败处理。
"""

from __future__ import annotations

import heapq
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class BatchError(Exception):
    """整批提交失败（下游抛出异常或返回协议非法）。"""


class BatchTimeout(Exception):
    """整批提交在超时时间内未返回。"""


class RequestCancelled(Exception):
    """请求在提交前被调用方取消。"""


class BatchClosedError(Exception):
    """批处理器已关闭，不再接受新请求。"""


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return "<UNSET>"


_UNSET = _Unset()


class Future:
    """单个调用方的结果句柄（非线程亲和，跨线程安全）。"""

    __slots__ = ("_event", "_result", "_exc", "_done", "_cancel_cb", "_lock")

    def __init__(self, on_cancel: Optional[Callable[[], None]] = None):
        self._event = threading.Event()
        self._result: Any = _UNSET
        self._exc: Optional[BaseException] = None
        self._done = False
        self._cancel_cb = on_cancel
        self._lock = threading.Lock()

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return isinstance(self._exc, RequestCancelled)

    def cancel(self) -> bool:
        """请求取消。未完成则被标记为 RequestCancelled 并返回 True。"""
        with self._lock:
            if self._done:
                return False
            self._done = True
            self._exc = RequestCancelled("request cancelled before dispatch")
            cb = self._cancel_cb
        self._event.set()
        if cb is not None:
            cb()
        return True

    def result(self, timeout: Optional[float] = None) -> Any:
        if not self._event.wait(timeout):
            raise TimeoutError("future.result() timed out")
        if self._exc is not None:
            raise self._exc
        return self._result

    def exception(self, timeout: Optional[float] = None) -> Optional[BaseException]:
        if not self._event.wait(timeout):
            raise TimeoutError("future.exception() timed out")
        return self._exc

    def _resolve(self, result: Any = _UNSET, exc: Optional[BaseException] = None) -> bool:
        with self._lock:
            if self._done:
                return False
            self._done = True
            if exc is not None:
                self._exc = exc
            else:
                self._result = result
        self._event.set()
        return True

    def set_result(self, result: Any) -> bool:
        return self._resolve(result=result)

    def set_exception(self, exc: BaseException) -> bool:
        return self._resolve(exc=exc)


class VirtualClock:
    """可注入的虚拟时钟 + 调度器：时间只在 advance 时前进，测试完全确定。

    同一时刻的到期任务按提交顺序（FIFO）执行，便于构造“边界同时到达”。
    """

    def __init__(self) -> None:
        self._now_ms = 0
        self._heap: List[tuple] = []
        self._seq = 0

    def now_ms(self) -> float:
        return float(self._now_ms)

    def call_now(self, fn: Callable[[], None]) -> None:
        fn()

    def schedule(self, delay_ms: float, fn: Callable[[], None]) -> object:
        self._seq += 1
        token = (self._now_ms + delay_ms, self._seq, fn)
        heapq.heappush(self._heap, token)
        return token

    def cancel(self, token: object) -> None:
        # 惰性取消：从堆中移除（条目数很少，线性删除即可）。
        try:
            self._heap.remove(token)  # type: ignore[arg-type]
            heapq.heapify(self._heap)
        except ValueError:
            pass

    def pending(self) -> int:
        return len(self._heap)

    def advance(self, ms: float) -> None:
        target = self._now_ms + ms
        while self._heap and self._heap[0][0] <= target:
            due, _seq, fn = heapq.heappop(self._heap)
            self._now_ms = due
            fn()
        self._now_ms = target


class RealScheduler:
    """生产用调度器：真实时间 + Timer 线程。"""

    def now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def call_now(self, fn: Callable[[], None]) -> None:
        fn()

    def schedule(self, delay_ms: float, fn: Callable[[], None]) -> object:
        timer = threading.Timer(delay_ms / 1000.0, fn)
        timer.daemon = True
        timer.start()
        return timer

    def cancel(self, token: object) -> None:
        token.cancel()  # type: ignore[attr-defined]


class _Entry:
    __slots__ = ("key", "futures", "settled")

    def __init__(self, key: Any):
        self.key = key
        self.futures: List[Future] = []
        self.settled = False


class Batcher:
    def __init__(
        self,
        handler: Callable[[List[Any], Callable[[Any], None]], None],
        max_batch_size: int,
        window_ms: float,
        timeout_ms: Optional[float] = None,
        scheduler: Optional[Any] = None,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        if window_ms < 0:
            raise ValueError("window_ms must be >= 0")
        self._handler = handler
        self._max = max_batch_size
        self._window = float(window_ms)
        self._timeout = None if timeout_ms is None else float(timeout_ms)
        self._clock = scheduler or RealScheduler()
        self._lock = threading.RLock()
        self._entries: Dict[Any, _Entry] = {}
        self._buffered: List[_Entry] = []
        self._timer_token: object = None
        self._timer_deadline: Optional[float] = None
        self._closed = False

    # ---- 公共 API -------------------------------------------------------

    def load(self, key: Any) -> Future:
        """请求一个键；窗口内 / 在途的同键请求合并为同一次下游调用。"""
        with self._lock:
            if self._closed:
                f = Future()
                f.set_exception(BatchClosedError("batcher is closed"))
                return f
            entry = self._entries.get(key)
            if entry is not None:
                f = Future()
                entry.futures.append(f)
                f._cancel_cb = lambda e=entry, ff=f: self._on_cancel(e, ff)
                return f

            entry = _Entry(key)
            f = Future()
            entry.futures.append(f)
            self._entries[key] = entry
            self._buffered.append(entry)
            f._cancel_cb = lambda e=entry, ff=f: self._on_cancel(e, ff)

            if len(self._buffered) >= self._max:
                # 数量阈值先到：立即提交（含按上限分片）。
                self._flush_locked()
            elif self._timer_token is None:
                self._arm_timer_locked()
            return f

    def flush(self) -> None:
        """立即提交当前缓冲中的所有请求（仍按 max_batch_size 分片）。"""
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        """关闭：拒绝新请求；缓冲中（尚未提交）的请求收到 BatchClosedError。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_timer_locked()
            for entry in self._buffered:
                self._entries.pop(entry.key, None)
                self._settle_entry(entry, exc=BatchClosedError("batcher closed before dispatch"))
            self._buffered.clear()

    # ---- 内部实现 -------------------------------------------------------

    def _on_cancel(self, entry: _Entry, future: Future) -> None:
        # 只摘除取消的这个调用方；同键的其他调用方不受影响。
        with self._lock:
            try:
                entry.futures.remove(future)
            except ValueError:
                return
            if entry.futures:
                return
            # 最后一个关心结果的调用方离开：后续同键请求按新请求处理。
            self._entries.pop(entry.key, None)
            if any(e is entry for e in self._buffered):
                # 尚在缓冲、从未提交：撤回该条目，不会产生下游调用。
                self._buffered.remove(entry)
                entry.settled = True
                if not self._buffered:
                    self._cancel_timer_locked()
            else:
                # 已在途：下游调用无法撤回；结果到达后无人等待即丢弃。
                entry.settled = True

    def _arm_timer_locked(self) -> None:
        if self._window <= 0:
            self._flush_locked()
            return
        self._timer_deadline = self._clock.now_ms() + self._window
        self._timer_token = self._clock.schedule(self._window, self._on_timer)

    def _cancel_timer_locked(self) -> None:
        if self._timer_token is not None:
            self._clock.cancel(self._timer_token)
        self._timer_token = None
        self._timer_deadline = None

    def _on_timer(self) -> None:
        with self._lock:
            self._timer_token = None
            self._timer_deadline = None
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffered:
            return
        self._cancel_timer_locked()
        ready = self._buffered
        self._buffered = []
        for idx in range(0, len(ready), self._max):
            shard = ready[idx: idx + self._max]
            self._dispatch_shard(shard)

    def _dispatch_shard(self, shard: List[_Entry]) -> None:
        keys = [entry.key for entry in shard]
        done = threading.Event()
        guard = threading.Lock()

        def complete(payload: Any = _UNSET) -> None:
            with guard:
                if done.is_set():
                    return
                done.set()
            self._on_shard_done(shard, payload)

        try:
            ret = self._handler(keys, complete)
        except BaseException as exc:  # handler 同步抛出 => 整批失败
            complete(exc)
            return
        if ret is not None:
            complete(ret)
        # 超时在 handler 注册之后入队：同一时刻完成优先于超时。
        if self._timeout is not None:
            self._clock.schedule(self._timeout, lambda: self._on_shard_timeout(shard, done, guard))

    def _on_shard_timeout(self, shard: List[_Entry], done: threading.Event, guard: "threading.Lock") -> None:
        with guard:
            if done.is_set():
                return
            done.set()
        exc = BatchTimeout("batch dispatch timed out")
        for entry in shard:
            self._settle_entry(entry, exc=exc)

    def _on_shard_done(self, shard: List[_Entry], payload: Any) -> None:
        if isinstance(payload, BaseException):
            exc = BatchError("batch dispatch failed")
            exc.__cause__ = payload
            for entry in shard:
                self._settle_entry(entry, exc=exc)
            return

        if not isinstance(payload, dict):
            exc = BatchError("handler must return a dict mapping key -> value/Exception")
            for entry in shard:
                self._settle_entry(entry, exc=exc)
            return

        for entry in shard:
            if entry.key not in payload:
                protocol_exc = BatchError(f"handler result missing key: {entry.key!r}")
                # 漏掉一个键视为下游协议错误：整批失败，避免调用方悬挂。
                for other in shard:
                    self._settle_entry(other, exc=protocol_exc)
                return

        for entry in shard:
            value = payload[entry.key]
            if isinstance(value, BaseException):
                self._settle_entry(entry, exc=value)
            else:
                self._settle_entry(entry, result=value)

    def _settle_entry(
        self,
        entry: _Entry,
        result: Any = _UNSET,
        exc: Optional[BaseException] = None,
    ) -> None:
        with self._lock:
            if entry.settled:
                return
            entry.settled = True
            self._entries.pop(entry.key, None)
            futures = list(entry.futures)
            entry.futures.clear()
        for future in futures:
            if exc is not None:
                future.set_exception(exc)
            else:
                future.set_result(result)
