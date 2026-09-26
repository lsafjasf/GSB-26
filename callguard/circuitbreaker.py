"""熔断器状态机：CLOSED -> OPEN -> HALF_OPEN -> CLOSED/OPEN。

统计口径：只统计「真实到达下游并被放行执行」的调用结果；
限流拒绝、熔断拒绝本身不进入统计（由组合层 Guard 保证不调用 on_*）。
"""
from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass


class State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Gate(enum.Enum):
    ALLOW = "allow"            # 放行（CLOSED）
    REJECT_OPEN = "reject_open"        # OPEN 冷却中，拒绝
    PROBE = "probe"            # HALF_OPEN，放行一次试探
    REJECT_HALF_OPEN = "reject_half_open"  # HALF_OPEN 试探名额已满，拒绝


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    state: State
    retry_after: float  # 拒绝时距状态可能变化（进入半开）的秒数；放行/试探为 0.0


class CircuitBreaker:
    def __init__(
        self,
        clock,
        *,
        failure_rate_threshold: float = 0.5,
        window_size: int = 10,
        minimum_calls: int = 5,
        open_duration: float = 30.0,
        half_open_max_probes: int = 1,
        half_open_successes_to_close: int = 1,
    ) -> None:
        if not 0 < failure_rate_threshold <= 1:
            raise ValueError("failure_rate_threshold must be in (0, 1]")
        if window_size <= 0 or minimum_calls <= 0 or minimum_calls > window_size:
            raise ValueError("require 0 < minimum_calls <= window_size")
        self._clock = clock
        self._threshold = failure_rate_threshold
        self._window_size = window_size
        self._minimum_calls = minimum_calls
        self._open_duration = open_duration
        self._half_open_max_probes = half_open_max_probes
        self._half_open_successes_to_close = half_open_successes_to_close

        self._state = State.CLOSED
        self._window: deque[bool] = deque(maxlen=window_size)  # True=失败
        self._opened_at: float | None = None
        self._inflight_probes = 0
        self._half_open_successes = 0

    @property
    def state(self) -> State:
        self._maybe_transition(self._clock.now())
        return self._state

    @property
    def failure_rate(self) -> float | None:
        if not self._window:
            return None
        return sum(self._window) / len(self._window)

    def _maybe_transition(self, now: float) -> None:
        if self._state is State.OPEN and self._opened_at is not None:
            if now - self._opened_at >= self._open_duration:
                self._state = State.HALF_OPEN
                self._inflight_probes = 0
                self._half_open_successes = 0

    def before_call(self) -> GateResult:
        now = self._clock.now()
        self._maybe_transition(now)
        if self._state is State.CLOSED:
            return GateResult(Gate.ALLOW, self._state, 0.0)
        if self._state is State.OPEN:
            elapsed = now - (self._opened_at or now)
            return GateResult(Gate.REJECT_OPEN, self._state, max(0.0, self._open_duration - elapsed))
        # HALF_OPEN
        if self._inflight_probes < self._half_open_max_probes:
            self._inflight_probes += 1
            return GateResult(Gate.PROBE, self._state, 0.0)
        return GateResult(Gate.REJECT_HALF_OPEN, self._state, 0.0)

    def cancel_probe(self) -> None:
        """试探名额已占用但未真正执行（如被限流拦截），归还名额。"""
        if self._state is State.HALF_OPEN:
            self._inflight_probes = max(0, self._inflight_probes - 1)

    def on_success(self) -> None:
        if self._state is State.HALF_OPEN:
            self._inflight_probes = max(0, self._inflight_probes - 1)
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_successes_to_close:
                self._close()
        else:
            self._record(False)

    def on_failure(self) -> None:
        if self._state is State.HALF_OPEN:
            self._inflight_probes = max(0, self._inflight_probes - 1)
            self._trip()  # 半开期间任何失败立即重新打开
        else:
            self._record(True)
            if self._should_trip():
                self._trip()

    def _record(self, failed: bool) -> None:
        self._window.append(failed)

    def _should_trip(self) -> bool:
        if len(self._window) < self._minimum_calls:
            return False
        return (sum(self._window) / len(self._window)) >= self._threshold

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock.now()
        self._window.clear()
        self._inflight_probes = 0
        self._half_open_successes = 0

    def _close(self) -> None:
        self._state = State.CLOSED
        self._opened_at = None
        self._window.clear()
        self._inflight_probes = 0
        self._half_open_successes = 0
