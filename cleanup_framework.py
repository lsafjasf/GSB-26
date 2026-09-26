"""中断处理与资源清理框架（仅依赖 Python 标准库）。

核心概念：
- CleanupStack：清理动作栈。动作按「逆注册序（LIFO）」执行，
  并支持 depends_on 声明依赖：X depends_on Y 表示 Y 必须比 X 后清理
  （即被依赖的资源比依赖它的资源活得更久）。
- 可重入 / 幂等：每个动作至多执行一次；close() 可重复调用，
  清理动作内部再次调用 close() 不会报错也不会二次执行。
- 失败隔离：单个清理动作抛异常会被记录，后续清理继续执行，
  close() 返回的 CleanupReport 汇总所有失败。
"""

from __future__ import annotations

import heapq
import signal
import threading
import time
import traceback as _traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set


class CleanupError(Exception):
    """框架级错误基类。"""


class CleanupOrderError(CleanupError):
    """依赖关系无法解析（未知依赖或依赖环）。"""


class StackClosedError(CleanupError):
    """向已关闭或正在关闭的栈注册新动作。"""


@dataclass
class CleanupFailure:
    """一次清理失败的完整记录。"""

    name: str
    error: BaseException
    traceback: str

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.name}: {type(self.error).__name__}: {self.error}"


@dataclass
class CleanupReport:
    """close() 的汇总报告。"""

    executed: List[str] = field(default_factory=list)   # 实际执行顺序
    failures: List[CleanupFailure] = field(default_factory=list)
    duration: float = 0.0                                # 总耗时（秒）

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        lines = [
            f"cleaned={len(self.executed)} failed={len(self.failures)} "
            f"duration={self.duration:.6f}s"
        ]
        for f in self.failures:
            lines.append(f"  FAILED {f}")
        return "\n".join(lines)


class _Action:
    __slots__ = ("name", "fn", "depends_on", "done")

    def __init__(self, name: str, fn: Callable[[], None], depends_on: Sequence[str]):
        self.name = name
        self.fn = fn
        self.depends_on = tuple(depends_on)
        self.done = False


class CleanupStack:
    """清理动作栈。线程安全；close() 幂等且可重入。"""

    def __init__(self) -> None:
        self._actions: Dict[str, _Action] = {}
        self._registration_order: List[str] = []
        self._lock = threading.RLock()
        self._closing = False
        self._closed = False
        self._report: Optional[CleanupReport] = None

    # ------------------------------------------------------------------ API

    def register(
        self,
        name: str,
        fn: Callable[[], None],
        depends_on: Sequence[str] = (),
    ) -> None:
        """注册一个清理动作。

        name: 唯一标识（用于依赖声明与失败报告）。
        fn: 无参可调用对象，必须自身幂等性无关——框架保证至多调用一次。
        depends_on: 本动作依赖（晚于本动作清理的）动作名列表。
        """
        with self._lock:
            if self._closed or self._closing:
                raise StackClosedError(
                    f"cannot register {name!r}: stack is closing/closed"
                )
            if name in self._actions:
                raise ValueError(f"duplicate cleanup action name: {name!r}")
            self._actions[name] = _Action(name, fn, depends_on)
            self._registration_order.append(name)

    def close(self) -> Optional[CleanupReport]:
        """执行全部清理动作并返回汇总报告。

        - 重复调用：返回首次调用的同一个报告，动作不会重复执行。
        - 重入调用（清理动作内部调用 close）：返回 None，不产生任何效果。
        - 单个动作抛异常：记录到报告并继续执行剩余动作。
        """
        with self._lock:
            if self._closed:
                return self._report
            if self._closing:
                return None  # 重入：忽略，由外层 close 完成
            self._closing = True

        report = CleanupReport()
        start = time.perf_counter()
        try:
            order = self._resolve_order(report)
            for name in order:
                action = self._actions[name]
                if action.done:  # 幂等保护：同一动作绝不执行两次
                    continue
                action.done = True  # 先标记，防止异常路径下的二次执行
                try:
                    action.fn()
                    report.executed.append(name)
                except BaseException as exc:  # 记录并继续
                    report.executed.append(name)
                    report.failures.append(
                        CleanupFailure(
                            name=name,
                            error=exc,
                            traceback=_traceback.format_exc(),
                        )
                    )
        finally:
            report.duration = time.perf_counter() - start
            with self._lock:
                self._report = report
                self._closed = True
                self._closing = False
        return report

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "CleanupStack":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False  # 不抑制业务异常

    # ------------------------------------------------------------- internal

    def _resolve_order(self, report: CleanupReport) -> List[str]:
        """计算执行顺序：LIFO 为基调，依赖关系优先。

        X depends_on Y  =>  X 先于 Y 执行。
        同等条件下，后注册的先执行（LIFO）。
        依赖环 / 未知依赖：记录一条失败并退化为纯 LIFO，保证清理仍然发生。
        """
        names = self._registration_order
        index = {n: i for i, n in enumerate(names)}
        deps: Dict[str, Set[str]] = {}
        dependents: Dict[str, Set[str]] = {n: set() for n in names}

        for n in names:
            known = set()
            for d in self._actions[n].depends_on:
                if d not in self._actions:
                    report.failures.append(
                        CleanupFailure(
                            name=n,
                            error=CleanupOrderError(f"unknown dependency {d!r}"),
                            traceback="",
                        )
                    )
                    continue
                known.add(d)
                dependents[d].add(n)
            deps[n] = known

        # Kahn 拓扑排序：先发出「没有未处理依赖者」的节点；
        # 并列时取注册序号最大者（LIFO）。
        remaining = {n: set(dependents[n]) for n in names}
        heap = [(-index[n], n) for n in names if not remaining[n]]
        heapq.heapify(heap)
        order: List[str] = []
        while heap:
            _, n = heapq.heappop(heap)
            order.append(n)
            for d in deps[n]:
                remaining[d].discard(n)
                if not remaining[d]:
                    heapq.heappush(heap, (-index[d], d))

        if len(order) != len(names):
            report.failures.append(
                CleanupFailure(
                    name="<resolve-order>",
                    error=CleanupOrderError(
                        "dependency cycle detected; falling back to plain LIFO"
                    ),
                    traceback="",
                )
            )
            return list(reversed(names))
        return order


@contextmanager
def signal_cleanup(stack: CleanupStack, sig: int = signal.SIGINT):
    """在 with 块内收到信号时，先执行 stack.close() 再传播中断。

    清理动作内再次收到信号是安全的：close() 幂等可重入。
    """
    previous = signal.getsignal(sig)

    def handler(signum, frame):
        stack.close()
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:
            raise KeyboardInterrupt

    signal.signal(sig, handler)
    try:
        yield stack
    finally:
        signal.signal(sig, previous)
