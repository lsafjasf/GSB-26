"""中断处理与资源清理框架（仅标准库）。

核心概念：
- CleanupStack：清理动作注册表。清理时按「注册的逆序」执行，
  并可用 depends_on 声明额外顺序约束（拓扑排序保证）。
- 可重入 / 幂等：每个动作有状态机（pending/running/done/failed），
  cleanup() 被重复调用或在清理动作内部重入调用都不会二次执行。
- 失败隔离：单个清理动作抛异常会被记录，后续清理继续执行，
  最终由 CleanupReport 汇总所有失败。
"""

from __future__ import annotations

import heapq
import signal
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple


class CleanupError(Exception):
    """框架级错误（重复注册、清理后注册、依赖环等）。"""


@dataclass
class CleanupRecord:
    """单个清理动作的执行记录。"""

    name: str
    status: str  # "done" | "failed"
    duration: float  # 秒
    error: Optional[BaseException] = None
    traceback_str: Optional[str] = None


@dataclass
class CleanupReport:
    """一次 cleanup() 的汇总报告。"""

    records: List[CleanupRecord] = field(default_factory=list)
    total_duration: float = 0.0

    @property
    def failures(self) -> List[CleanupRecord]:
        return [r for r in self.records if r.status == "failed"]

    @property
    def succeeded(self) -> List[CleanupRecord]:
        return [r for r in self.records if r.status == "done"]

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        lines = [
            f"cleanup report: {len(self.succeeded)} ok, "
            f"{len(self.failures)} failed, "
            f"total {self.total_duration * 1000:.3f} ms"
        ]
        for rec in self.failures:
            lines.append(f"  FAILED: {rec.name}: {rec.error!r}")
        return "\n".join(lines)


class _Action:
    __slots__ = ("name", "fn", "depends_on", "state", "error")

    def __init__(self, name: str, fn: Callable[[], None],
                 depends_on: Sequence[str]) -> None:
        self.name = name
        self.fn = fn
        self.depends_on = tuple(depends_on)
        self.state = "pending"  # pending -> running -> done | failed
        self.error: Optional[BaseException] = None


class CleanupStack:
    """清理动作栈：逆序执行、依赖声明、可重入、失败汇总。

    用法：
        stack = CleanupStack()
        stack.register("close-db", db.close)
        stack.register("flush-log", log.flush, depends_on=["close-db"])
        report = stack.cleanup()   # 幂等，可重复调用

    或作为上下文管理器（退出时自动清理，异常也会清理）：
        with CleanupStack() as stack:
            ...
    """

    def __init__(self) -> None:
        self._actions: Dict[str, _Action] = {}
        self._order: List[str] = []  # 注册顺序
        self._lock = threading.RLock()
        self._cleaning = False       # 正在执行 cleanup（重入检测）
        self._report: Optional[CleanupReport] = None

    # ------------------------------------------------------------------ 注册

    def register(self, name: str, fn: Callable[[], None],
                 depends_on: Sequence[str] = ()) -> Callable[[], None]:
        """注册清理动作。depends_on 中的动作保证先于本动作执行。"""
        with self._lock:
            if self._report is not None or self._cleaning:
                raise CleanupError(
                    f"cannot register {name!r}: cleanup already started/finished")
            if name in self._actions:
                raise CleanupError(f"duplicate cleanup action: {name!r}")
            self._actions[name] = _Action(name, fn, depends_on)
            self._order.append(name)
        return fn

    def cleanup_action(self, name: str,
                       depends_on: Sequence[str] = ()) -> Callable:
        """装饰器形式的 register。"""
        def deco(fn: Callable[[], None]) -> Callable[[], None]:
            return self.register(name, fn, depends_on)
        return deco

    def unregister(self, name: str) -> None:
        """注销动作（资源已成功移交/不再需要清理时不执行它）。"""
        with self._lock:
            if name in self._actions:
                del self._actions[name]
                self._order.remove(name)

    def pending_names(self) -> List[str]:
        with self._lock:
            return [n for n in self._order
                    if self._actions[n].state == "pending"]

    # -------------------------------------------------------------- 顺序解析

    def _resolve_order(self) -> List[str]:
        """拓扑排序：以注册逆序为基准，同时满足 depends_on 约束。"""
        names = list(self._order)
        index = {n: i for i, n in enumerate(names)}
        indeg = {n: 0 for n in names}
        adj: Dict[str, List[str]] = {n: [] for n in names}
        for n in names:
            for dep in self._actions[n].depends_on:
                if dep not in self._actions:
                    raise CleanupError(
                        f"{n!r} depends on unknown action {dep!r}")
                adj[dep].append(n)  # dep 必须先于 n 清理
                indeg[n] += 1
        # 堆键为注册序的负数 -> 优先弹出“后注册”的动作（逆序基准）
        heap: List[Tuple[int, str]] = [
            (-index[n], n) for n in names if indeg[n] == 0]
        heapq.heapify(heap)
        resolved: List[str] = []
        while heap:
            _, n = heapq.heappop(heap)
            resolved.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    heapq.heappush(heap, (-index[m], m))
        if len(resolved) != len(names):
            remaining = [n for n in names if indeg[n] > 0]
            raise CleanupError(f"dependency cycle among: {remaining}")
        return resolved

    # ------------------------------------------------------------------ 执行

    def cleanup(self) -> Optional[CleanupReport]:
        """执行所有待清理动作，返回汇总报告。

        - 幂等：完成后再次调用直接返回同一份报告，不重复执行。
        - 可重入：清理动作内部（或信号处理器中）再次调用时，
          立即返回 None，由外层调用继续完成剩余清理。
        """
        with self._lock:
            if self._report is not None:
                return self._report
            if self._cleaning:
                return None  # 重入：外层 cleanup 正在进行
            self._cleaning = True

        report = CleanupReport()
        start_all = time.perf_counter()
        try:
            for name in self._resolve_order():
                with self._lock:
                    act = self._actions[name]
                    if act.state != "pending":
                        continue  # 已被重入路径处理，跳过
                    act.state = "running"
                start = time.perf_counter()
                try:
                    act.fn()
                except BaseException as exc:  # 记录并继续后续清理
                    act.state = "failed"
                    act.error = exc
                    report.records.append(CleanupRecord(
                        name=name, status="failed",
                        duration=time.perf_counter() - start,
                        error=exc, traceback_str=traceback.format_exc()))
                else:
                    act.state = "done"
                    report.records.append(CleanupRecord(
                        name=name, status="done",
                        duration=time.perf_counter() - start))
        finally:
            report.total_duration = time.perf_counter() - start_all
            with self._lock:
                self._cleaning = False
                if self._report is None:
                    self._report = report
                else:
                    report = self._report
        return report

    close = cleanup

    # ------------------------------------------------------------ 上下文管理

    def __enter__(self) -> "CleanupStack":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.cleanup()
        return False  # 不吞掉业务异常


# ------------------------------------------------------------------ 信号集成

@contextmanager
def signal_cleanup(stack: CleanupStack,
                   sigs: Sequence[int] = (signal.SIGINT, signal.SIGTERM)):
    """在 with 块内收到指定信号时：先执行 stack.cleanup()，再抛出
    InterruptedError 中断当前任务。退出 with 块后恢复原信号处理器。

    重复信号安全：CleanupStack.cleanup 幂等且可重入。
    """
    def handler(signum, frame):
        stack.cleanup()
        raise InterruptedError(f"interrupted by signal {signum}")

    previous = {s: signal.signal(s, handler) for s in sigs}
    try:
        yield stack
    finally:
        for s, h in previous.items():
            signal.signal(s, h)
