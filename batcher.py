"""请求批处理与合并库（仅标准库）。

核心能力：
- 数量阈值与时间窗口双触发，先到先触发；
- 同一键（key）的并发请求合并为一条下游请求，所有调用方共享结果；
- 批量结果逐条分发：单条失败不影响其他条目；
- 批量整体失败或超时时，所有等待方收到可区分的异常，无人挂起；
- 支持调用方取消（Future.cancel）。

时间注入：窗口定时通过 scheduler 抽象（`call_later(delay, fn) -> handle`，
handle 需有 `cancel()`）。生产用 `ThreadingScheduler`，测试用
`ManualScheduler`（见 test_batcher.py）精确推进虚拟时间。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, InvalidStateError
from typing import Any, Callable, Dict, Hashable, List, Optional


class BatchTimeoutError(TimeoutError):
    """批量处理超过 timeout 时，分发给该批所有等待方的异常。"""


class BatchResultError(Exception):
    """handler 返回值非法（不是与请求等长的序列）时分发给所有等待方。"""


class ThreadingScheduler:
    """生产环境调度器：基于 threading.Timer。"""

    def call_later(self, delay: float, callback: Callable[[], None]):
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return timer


class _Item:
    """一条待处理的下游请求。waiters 为等待该结果的调用方 Future 列表
    （同键合并时可有多个）。"""

    __slots__ = ("key", "request", "waiters")

    def __init__(self, key: Hashable, request: Any):
        self.key = key
        self.request = request
        self.waiters: List[Future] = []


class Batcher:
    """按 key 合并、按数量/时间窗口触发批量提交的请求批处理器。

    handler: Callable[[List[Any]], List[Any]]，接收本批请求列表，
        必须返回等长列表；列表中某个元素若是 BaseException 实例，
        则作为该条子请求的失败原因回传给对应调用方，不影响其他条目。
        handler 抛异常 => 整体失败，广播给本批所有等待方。
    max_batch_size: 触发批量提交的数量阈值，也是每批的最大分片大小。
    window: 时间窗口（秒）。窗口内积累请求，窗口到期即提交。
    timeout: 单次批量提交的最大耗时（秒），超时广播 BatchTimeoutError。
        None 表示不限制。
    scheduler: 定时器抽象，默认 ThreadingScheduler；测试可注入虚拟时钟。
    """

    def __init__(
        self,
        handler: Callable[[List[Any]], List[Any]],
        *,
        max_batch_size: int,
        window: float,
        timeout: Optional[float] = None,
        scheduler: Optional[Any] = None,
    ):
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be >= 1")
        if window <= 0:
            raise ValueError("window must be > 0")
        self._handler = handler
        self._max_batch_size = max_batch_size
        self._window = window
        self._timeout = timeout
        self._scheduler = scheduler or ThreadingScheduler()

        self._lock = threading.Lock()
        self._pending: List[_Item] = []
        self._by_key: Dict[Hashable, _Item] = {}
        self._timer = None

        # 统计：用于“合并前后下游调用次数”对比
        self._stats_lock = threading.Lock()
        self._stats = {
            "submitted": 0,        # 调用方提交的逻辑请求数（含被合并的）
            "merged": 0,           # 因同键合并而未下行的请求数
            "cancelled": 0,        # 提交后被取消的请求数
            "downstream_calls": 0, # 实际调用 handler 的次数（批次数）
            "downstream_items": 0, # 实际下行的条目数
        }

    # ------------------------------------------------------------------ API

    def submit(self, key: Hashable, request: Any = None) -> Future:
        """提交一个请求，返回 Future。

        - 同 key 且尚未提交的请求会被合并：共享同一条下游请求与结果；
        - Future 的结果为 handler 返回的对应元素；失败时通过
          future.exception() / result() 抛出拿到：
          * 子请求失败：handler 返回的异常实例（原样回传）；
          * 整体失败：handler 抛出的异常；
          * 超时：BatchTimeoutError；
          * 取消：concurrent.futures.CancelledError。
        """
        future: Future = Future()
        with self._lock:
            self._bump("submitted")
            item = self._by_key.get(key)
            if item is None:
                item = _Item(key, request)
                self._by_key[key] = item
                self._pending.append(item)
                if len(self._pending) == 1:
                    self._arm_timer_locked()
            else:
                self._bump("merged")
            item.waiters.append(future)
            chunks = []
            if len(self._pending) >= self._max_batch_size:
                chunks = self._collect_flush_locked()
        future.add_done_callback(lambda f: self._on_waiter_done(f, item))
        self._run_chunks(chunks)
        return future

    def call(self, key: Hashable, request: Any = None, timeout: Optional[float] = None) -> Any:
        """阻塞式便捷接口：提交并等待结果。"""
        return self.submit(key, request).result(timeout=timeout)

    @property
    def stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    # ------------------------------------------------------------- internal

    def _bump(self, name: str, delta: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] += delta

    def _arm_timer_locked(self) -> None:
        self._cancel_timer_locked()
        self._timer = self._scheduler.call_later(self._window, self._on_timer)

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self) -> None:
        with self._lock:
            chunks = self._collect_flush_locked()
        self._run_chunks(chunks)

    def _collect_flush_locked(self) -> List[List[_Item]]:
        """（持锁调用）取出当前 pending 并按 max_batch_size 分片；
        实际的下游调用在锁外执行，避免结算时重入死锁。"""
        if not self._pending:
            self._cancel_timer_locked()
            return []
        items = self._pending
        self._pending = []
        self._by_key.clear()
        self._cancel_timer_locked()
        chunks = []
        for start in range(0, len(items), self._max_batch_size):
            chunk = items[start:start + self._max_batch_size]
            self._bump("downstream_calls")
            self._bump("downstream_items", len(chunk))
            chunks.append(chunk)
        return chunks

    def _run_chunks(self, chunks: List[List[_Item]]) -> None:
        for chunk in chunks:
            self._run_batch(chunk)

    def _run_batch(self, items: List[_Item]) -> None:
        """在独立线程中执行 handler，支持超时。超时后 handler 线程可能
        仍在后台运行，其结果会被丢弃（等待方已收到 BatchTimeoutError）。"""
        done = threading.Event()
        outcome: Dict[str, Any] = {}

        def target() -> None:
            try:
                outcome["results"] = self._handler([it.request for it in items])
            except BaseException as exc:  # 整体失败
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=target, daemon=True)
        worker.start()

        if not done.wait(self._timeout):
            self._fail_batch(items, BatchTimeoutError(
                f"batch of {len(items)} item(s) exceeded timeout "
                f"{self._timeout!r}s"))
            return

        if "error" in outcome:
            self._fail_batch(items, outcome["error"])
            return

        results = outcome.get("results")
        if not isinstance(results, (list, tuple)) or len(results) != len(items):
            self._fail_batch(items, BatchResultError(
                f"handler must return a list of {len(items)} result(s), "
                f"got {results!r}"))
            return

        for item, result in zip(items, results):
            if isinstance(result, BaseException):
                self._settle(item, exception=result)
            else:
                self._settle(item, value=result)

    def _settle(self, item: _Item, value: Any = None,
                exception: Optional[BaseException] = None) -> None:
        with self._lock:
            waiters = list(item.waiters)
        for future in waiters:
            try:
                if exception is not None:
                    future.set_exception(exception)
                else:
                    future.set_result(value)
            except InvalidStateError:
                pass  # 已被取消或已结算

    def _fail_batch(self, items: List[_Item], exc: BaseException) -> None:
        for item in items:
            self._settle(item, exception=exc)

    def _on_waiter_done(self, future: Future, item: _Item) -> None:
        """调用方取消时，把它从等待列表移除；若该条目已无人等待且尚未
        提交，则整条移除（不下行）。"""
        if not future.cancelled():
            return
        with self._lock:
            self._bump("cancelled")
            if future in item.waiters:
                item.waiters.remove(future)
            if not item.waiters and item in self._pending:
                self._pending.remove(item)
                self._by_key.pop(item.key, None)
                if not self._pending:
                    self._cancel_timer_locked()
