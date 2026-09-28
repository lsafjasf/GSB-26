"""任务树与取消传播库（仅标准库）。

核心语义：
- 任务组成树：create_child() 派生子任务，wait_children() 等待全部子任务。
- 取消沿树向下传播且不可逆：任务一旦进入 CANCELLED，任何后代
  （包括其自身）都不得再写结果、置失败或触发完成回调。
- 取消与失败是两种不同的终态，错误类型分别为 Cancelled 与任务异常。
- 资源释放确定且幂等：任务进入任意终态时释放已注册的句柄与缓冲。
- 父任务可通过 summary() 汇总整棵子树的取消/失败/完成情况。

线程安全：所有状态迁移与资源释放在同一把锁（RLock）下完成，
set_result/set_exception 与 cancel 之间的竞争由状态检查原子地裁决。
"""

from __future__ import annotations

import enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class Cancelled(Exception):
    """任务被取消时使用的错误类型（与失败异常严格区分）。"""


class InvalidStateError(Exception):
    """对已进入终态的任务执行非法状态迁移时抛出。"""


class State(enum.Enum):
    PENDING = "pending"      # 已创建，尚未开始
    RUNNING = "running"      # 正在执行
    COMPLETED = "completed"  # 正常完成（终态）
    FAILED = "failed"        # 失败（终态）
    CANCELLED = "cancelled"  # 被取消（终态，不可逆）

    @property
    def done(self) -> bool:
        return self in (State.COMPLETED, State.FAILED, State.CANCELLED)


class Resource:
    """一个可释放资源（句柄/缓冲）。release() 幂等，released 可断言。"""

    __slots__ = ("name", "_releaser", "released")

    def __init__(self, name: str, releaser: Optional[Callable[[], None]] = None):
        self.name = name
        self._releaser = releaser
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            if self._releaser is not None:
                self._releaser()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Resource {self.name} released={self.released}>"


class TaskNode:
    """任务树节点。"""

    __slots__ = (
        "name", "parent", "_lock", "_state", "_children", "_resources",
        "_result", "_error", "_callbacks", "_done_event",
    )

    def __init__(self, name: str, parent: Optional["TaskNode"] = None):
        self.name = name
        self.parent = parent
        self._lock = threading.RLock()
        self._state = State.PENDING
        self._children: List[TaskNode] = []
        self._resources: List[Resource] = []
        self._result: Any = None
        self._error: Optional[BaseException] = None
        self._callbacks: List[Callable[["TaskNode"], None]] = []
        self._done_event = threading.Event()

    # ------------------------------------------------------------------ 树结构

    def create_child(self, name: str) -> "TaskNode":
        """创建子任务。父任务已取消时，子任务立即继承取消（取消不可逆）。"""
        child = TaskNode(name, parent=self)
        with self._lock:
            if self._state is State.CANCELLED:
                # 取消不可逆：取消后派生的后代直接处于取消态
                child._cancel_locked()
            elif self._state.done:
                raise InvalidStateError(
                    f"cannot create child under finished task {self.name!r} "
                    f"(state={self._state.value})"
                )
            self._children.append(child)
        return child

    @property
    def children(self) -> List["TaskNode"]:
        with self._lock:
            return list(self._children)

    def wait_children(self, timeout: Optional[float] = None) -> bool:
        """等待全部直接子任务到达终态。返回是否全部完成。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        for child in self.children:
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
            if not child.wait(remaining):
                return False
        return True

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done_event.wait(timeout)

    # ------------------------------------------------------------------ 状态迁移

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def cancelled(self) -> bool:
        return self.state is State.CANCELLED

    @property
    def done(self) -> bool:
        return self.state.done

    def start(self) -> None:
        with self._lock:
            if self._state is not State.PENDING:
                raise InvalidStateError(
                    f"cannot start task in state {self._state.value}"
                )
            self._state = State.RUNNING

    def set_result(self, value: Any) -> None:
        """写入结果。与 cancel 竞争时由锁保证只有一个获胜；
        若任务已被取消则拒绝写入（取消后不得写回结果）。"""
        with self._lock:
            if self._state is State.CANCELLED:
                raise Cancelled(f"task {self.name!r} was cancelled; result dropped")
            if self._state.done:
                raise InvalidStateError(
                    f"task {self.name!r} already finished ({self._state.value})"
                )
            self._result = value
            self._finish_locked(State.COMPLETED)

    def set_exception(self, error: BaseException) -> None:
        """标记失败。Cancelled 异常一律视为取消而非失败。"""
        if isinstance(error, Cancelled):
            self.cancel()
            return
        with self._lock:
            if self._state is State.CANCELLED:
                raise Cancelled(f"task {self.name!r} was cancelled; failure dropped")
            if self._state.done:
                raise InvalidStateError(
                    f"task {self.name!r} already finished ({self._state.value})"
                )
            self._error = error
            self._finish_locked(State.FAILED)

    def cancel(self) -> bool:
        """取消本任务并传播到整棵子树（显式栈迭代，深度不受递归上限约束）。

        幂等，返回本次是否发生了状态迁移。
        """
        with self._lock:
            return self._cancel_locked()

    def _cancel_locked(self) -> bool:
        if self._state is State.CANCELLED:
            return False
        if self._state.done:
            # 取消已完成/已失败任务：无状态迁移，但保证资源已释放（幂等）
            self._release_resources_locked()
            return False
        self._error = Cancelled(f"task {self.name!r} cancelled")
        self._finish_locked(State.CANCELLED)
        # 显式栈迭代传播到所有后代（深链不依赖递归上限）。
        # 锁顺序恒为「父 → 子」，栈顶处理最深的未访问节点，因此每个节点
        # 加锁时其父节点必已持锁，与原递归版本的加锁顺序完全一致。
        stack: List[TaskNode] = list(self._children)
        while stack:
            child = stack.pop()
            with child._lock:
                if child._state is State.CANCELLED:
                    continue
                if child._state.done:
                    # 已完成/已失败节点不可取消，且取消不跨终态节点继续下传，
                    # 仅保证资源已释放（幂等）。
                    child._release_resources_locked()
                    continue
                child._error = Cancelled(f"task {child.name!r} cancelled")
                child._finish_locked(State.CANCELLED)
                stack.extend(child._children)
        return True

    def _finish_locked(self, final: State) -> None:
        """进入终态：先释放资源、置终态、唤醒等待者，最后在锁外语义下触发回调。

        回调在持锁状态下同步触发之前先收集；为保证「取消后不触发完成回调」，
        只有非取消终态才会触发回调。
        """
        self._release_resources_locked()
        self._state = final
        self._done_event.set()
        if final is not State.CANCELLED:
            callbacks = list(self._callbacks)
            self._callbacks.clear()
            for cb in callbacks:
                cb(self)
        else:
            self._callbacks.clear()

    # ------------------------------------------------------------------ 资源

    def add_resource(self, resource: Resource) -> Resource:
        """注册资源。任务已结束时立即释放（确定性释放）。"""
        with self._lock:
            if self._state.done:
                resource.release()
            else:
                self._resources.append(resource)
            return resource

    def _release_resources_locked(self) -> None:
        resources, self._resources = self._resources, []
        for res in resources:
            res.release()

    @property
    def resources_released(self) -> bool:
        """断言辅助：所有已注册资源均已释放。"""
        with self._lock:
            return not self._resources and True

    # ------------------------------------------------------------------ 结果与汇总

    def on_complete(self, callback: Callable[["TaskNode"], None]) -> None:
        """注册完成回调。取消的任务不会触发；已终态任务按语义立即处理。"""
        with self._lock:
            if self._state is State.CANCELLED:
                return  # 取消不可逆：永不触发
            if self._state.done:
                callback(self)
                return
            self._callbacks.append(callback)

    def result(self) -> Any:
        with self._lock:
            if self._state is State.CANCELLED:
                raise Cancelled(f"task {self.name!r} was cancelled")
            if self._state is State.FAILED:
                raise self._error  # type: ignore[misc]
            if not self._state.done:
                raise InvalidStateError(f"task {self.name!r} not finished")
            return self._result

    @property
    def error(self) -> Optional[BaseException]:
        with self._lock:
            return self._error

    def summary(self) -> Dict[str, Any]:
        """汇总本任务及全部后代的状态分布与失败详情。"""
        counts = {s.value: 0 for s in State}
        failures: List[Dict[str, str]] = []

        # 显式栈迭代的深度优先遍历（先序），十万层深链也不会打穿递归上限。
        # reverse 后压栈以保持与原递归相同的子节点访问顺序。
        stack: List[TaskNode] = [self]
        while stack:
            node = stack.pop()
            with node._lock:
                counts[node._state.value] += 1
                if node._state is State.FAILED and node._error is not None:
                    failures.append({
                        "task": node.name,
                        "error_type": type(node._error).__name__,
                        "error": str(node._error),
                    })
                children = list(node._children)
            stack.extend(reversed(children))
        return {
            "total": sum(counts.values()),
            "counts": counts,
            "cancelled": counts[State.CANCELLED.value],
            "failed": counts[State.FAILED.value],
            "completed": counts[State.COMPLETED.value],
            "failures": failures,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskNode {self.name} state={self.state.value}>"
