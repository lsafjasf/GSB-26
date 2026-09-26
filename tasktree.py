"""Task tree with cancellation propagation (Python 3 stdlib only).

Guarantees:
- Cancelling a node cancels its whole subtree; cancellation is irreversible.
- After cancellation, no descendant can write a result or fire completion
  callbacks (state transitions are atomic per node under its lock).
- Cancellation and failure are distinct states with distinct error types.
- Resources (handles / buffers) are released exactly once, deterministically,
  on the first terminal transition; repeated cancels and cancel-after-finish
  are no-ops.
- All subtree traversals are iterative, so deep trees never hit the
  recursion limit.
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class State(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({State.COMPLETED, State.FAILED, State.CANCELLED})


class TaskCancelledError(Exception):
    """Recorded as ``node.error`` when a task is cancelled."""


class TaskFailedError(Exception):
    """Wraps an arbitrary failure with the failing task attached."""

    def __init__(self, task: "TaskNode", cause: BaseException):
        super().__init__(f"task {task.name!r} failed: {cause!r}")
        self.task = task
        self.cause = cause


class Summary:
    """Aggregate view of a subtree: counts per state plus failed/cancelled nodes."""

    __slots__ = ("total", "counts", "failures", "cancellations")

    def __init__(self, counts, failures, cancellations):
        self.counts = counts
        self.total = sum(counts.values())
        self.failures = failures
        self.cancellations = cancellations

    @property
    def completed(self) -> int:
        return self.counts[State.COMPLETED]

    @property
    def failed(self) -> int:
        return self.counts[State.FAILED]

    @property
    def cancelled(self) -> int:
        return self.counts[State.CANCELLED]

    @property
    def pending(self) -> int:
        return self.counts[State.PENDING] + self.counts[State.RUNNING]

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        parts = ", ".join(f"{s.value}={self.counts[s]}" for s in State)
        return f"Summary(total={self.total}, {parts})"


class TaskNode:
    """One node in a task tree."""

    __slots__ = (
        "name",
        "parent",
        "children",
        "state",
        "result",
        "error",
        "_lock",
        "_done",
        "_handles",
        "_buffers",
        "_callbacks",
        "_resources_released",
        "_before_finalize",
    )

    def __init__(self, name: str = ""):
        self.name = name
        self.parent: TaskNode | None = None
        self.children: list[TaskNode] = []
        self.state = State.PENDING
        self.result = None
        self.error: BaseException | None = None
        self._lock = threading.Lock()
        self._done = threading.Condition(self._lock)
        self._handles: list = []
        self._buffers: list = []
        self._callbacks: list = []
        self._resources_released = False
        # Test hook: called inside the lock right before a terminal
        # transition, to force deterministic race interleavings.
        self._before_finalize = None

    # ------------------------------------------------------------------ tree

    def spawn(self, name: str = "") -> "TaskNode":
        """Create a child task. A child spawned under a cancelled parent is
        born cancelled, so cancellation remains irreversible."""
        child = TaskNode(name)
        with self._lock:
            child.parent = self
            self.children.append(child)
            if self.state is State.CANCELLED:
                child.state = State.CANCELLED
                child.error = TaskCancelledError(
                    f"parent {self.name!r} was already cancelled"
                )
                child._resources_released = True
        return child

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            if self.state is State.PENDING:
                self.state = State.RUNNING

    def complete(self, result=None) -> bool:
        """Mark completed. Returns False (and writes nothing) if the task
        already reached a terminal state, e.g. lost a race with cancel()."""
        return self._finalize(State.COMPLETED, result=result, error=None)

    def fail(self, exc: BaseException) -> bool:
        """Mark failed with a distinct error type. Returns False if terminal."""
        return self._finalize(State.FAILED, result=None, error=exc)

    def cancel(self) -> None:
        """Cancel this node and its whole subtree. Irreversible; safe to call
        repeatedly; a no-op on nodes that already finished."""
        stack = [self]
        while stack:
            node = stack.pop()
            with node._lock:
                if node.state in TERMINAL_STATES:
                    continue
                node.state = State.CANCELLED
                node.error = TaskCancelledError(f"task {node.name!r} cancelled")
                node._release_resources_locked()
                kids = list(node.children)
                node._done.notify_all()
            node._notify_ancestors()
            stack.extend(kids)

    def cancel_self(self) -> None:
        """Self-cancellation: identical to cancel(), named for readability
        when a task cancels itself from its own worker."""
        self.cancel()

    def _finalize(self, state: State, result, error) -> bool:
        with self._lock:
            if self.state in TERMINAL_STATES:
                return False
            if self._before_finalize is not None:
                self._before_finalize(self)
            self.state = state
            self.result = result
            self.error = error
            self._release_resources_locked()
            callbacks = list(self._callbacks)
            self._done.notify_all()
        for cb in callbacks:
            cb(self)
        self._notify_ancestors()
        return True

    # -------------------------------------------------------------- waiting

    def wait(self, timeout: float | None = None) -> State:
        """Wait until this node reaches a terminal state; return the state."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._done:
            while self.state not in TERMINAL_STATES:
                if deadline is None:
                    self._done.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"task {self.name!r} did not finish")
                    self._done.wait(remaining)
            return self.state

    def wait_all(self, timeout: float | None = None) -> Summary:
        """Wait until every *descendant* is terminal (self excluded, so an
        empty tree returns immediately); return an aggregate Summary of the
        whole subtree including self."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._done:
                if self._subtree_terminal():
                    return self.summary()
                if deadline is None:
                    self._done.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("subtree did not finish in time")
                    self._done.wait(remaining)

    def _subtree_terminal(self) -> bool:
        stack = list(self.children)
        while stack:
            node = stack.pop()
            if node.state not in TERMINAL_STATES:
                return False
            stack.extend(node.children)
        return True

    def _notify_ancestors(self) -> None:
        node = self.parent
        while node is not None:
            with node._done:
                node._done.notify_all()
            node = node.parent

    # ------------------------------------------------------------ aggregation

    def summary(self) -> Summary:
        counts = {s: 0 for s in State}
        failures: list[TaskNode] = []
        cancellations: list[TaskNode] = []
        stack = [self]
        while stack:
            node = stack.pop()
            counts[node.state] += 1
            if node.state is State.FAILED:
                failures.append(node)
            elif node.state is State.CANCELLED:
                cancellations.append(node)
            stack.extend(node.children)
        return Summary(counts, failures, cancellations)

    # ------------------------------------------------------------- resources

    def register_handle(self, handle) -> None:
        """Register a handle (must expose close()); closed on termination."""
        with self._lock:
            if self.state in TERMINAL_STATES:
                raise RuntimeError(
                    f"cannot register handle on finished task {self.name!r}"
                )
            self._handles.append(handle)

    def register_buffer(self, buf) -> None:
        """Register a buffer; cleared and dropped on termination."""
        with self._lock:
            if self.state in TERMINAL_STATES:
                raise RuntimeError(
                    f"cannot register buffer on finished task {self.name!r}"
                )
            self._buffers.append(buf)

    def _release_resources_locked(self) -> None:
        if self._resources_released:
            return
        self._resources_released = True
        handles, self._handles = self._handles, []
        buffers, self._buffers = self._buffers, []
        for h in handles:
            h.close()
        for b in buffers:
            clear = getattr(b, "clear", None)
            if callable(clear):
                clear()

    @property
    def resources_released(self) -> bool:
        return self._resources_released

    # ------------------------------------------------------------ callbacks

    def add_done_callback(self, fn) -> None:
        """fn(node) fires on COMPLETED or FAILED only -- never on CANCELLED."""
        with self._lock:
            if self.state in (State.COMPLETED, State.FAILED):
                fire_now = True
            elif self.state is State.CANCELLED:
                return
            else:
                fire_now = False
                self._callbacks.append(fn)
        if fire_now:
            fn(self)

    # ------------------------------------------------------------ accessors

    @property
    def cancelled(self) -> bool:
        return self.state is State.CANCELLED

    @property
    def failed(self) -> bool:
        return self.state is State.FAILED

    @property
    def done(self) -> bool:
        return self.state in TERMINAL_STATES

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"TaskNode(name={self.name!r}, state={self.state.value})"
