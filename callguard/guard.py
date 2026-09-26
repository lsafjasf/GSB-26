"""组合调用保护：决策顺序为 熔断器 -> 限流器 -> 执行 -> 结果回报熔断器。

交互规则（关键）：
1. 先问熔断器：OPEN 拒绝 / HALF_OPEN 限量试探 / CLOSED 放行。
2. 熔断器放行后再问限流器：令牌不足则限流拒绝。
3. 限流拒绝的请求【不会】调用 breaker.on_failure()，因此不计入失败统计，
   避免「限流期间的拒绝把熔断器提前打开」。
4. 只有真正放行的调用，其成功/失败结果才回报熔断器。
5. 同一输入序列 + ManualClock => 决策序列完全可复现（无任何随机与真实时间）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Callable

from .circuitbreaker import CircuitBreaker, Gate
from .ratelimiter import TokenBucketRateLimiter


class Decision(enum.Enum):
    ALLOWED = "allowed"                    # 放行并执行
    RATE_LIMITED = "rate_limited"          # 限流拒绝（不计入熔断统计）
    CIRCUIT_OPEN = "circuit_open"          # 熔断拒绝（冷却中）
    HALF_OPEN_PROBE = "half_open_probe"    # 半开试探（放行执行）
    HALF_OPEN_BUSY = "half_open_busy"      # 半开试探名额已满，拒绝


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    reason: str
    retry_after: float
    executed: bool
    succeeded: bool | None  # 未执行为 None
    error: Exception | None = None


class CallGuard:
    def __init__(self, limiter: TokenBucketRateLimiter, breaker: CircuitBreaker) -> None:
        self.limiter = limiter
        self.breaker = breaker
        self.history: list[Outcome] = []

    def call(self, fn: Callable[[], Any]) -> Outcome:
        # 1) 熔断器先行
        gate = self.breaker.before_call()
        if gate.gate is Gate.REJECT_OPEN:
            return self._emit(Decision.CIRCUIT_OPEN, "circuit open, cooling down",
                              gate.retry_after, executed=False, succeeded=None)
        if gate.gate is Gate.REJECT_HALF_OPEN:
            return self._emit(Decision.HALF_OPEN_BUSY, "half-open probe slots exhausted",
                              gate.retry_after, executed=False, succeeded=None)
        probe = gate.gate is Gate.PROBE

        # 2) 限流器其次；被拒绝时不触碰熔断器统计
        acquire = self.limiter.try_acquire()
        if not acquire.allowed:
            if probe:
                self.breaker.cancel_probe()  # 未执行的试探不占用名额
            return self._emit(
                Decision.HALF_OPEN_PROBE if probe else Decision.RATE_LIMITED,
                ("half-open probe " if probe else "") + "rate limited, "
                f"retry after {acquire.retry_after:.3f}s",
                acquire.retry_after, executed=False, succeeded=None)

        # 3) 执行并回报熔断器
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 任何下游异常都算失败
            self.breaker.on_failure()
            return self._emit(Decision.HALF_OPEN_PROBE if probe else Decision.ALLOWED,
                              f"executed, failed: {exc!r}", 0.0,
                              executed=True, succeeded=False, error=exc)
        self.breaker.on_success()
        return self._emit(Decision.HALF_OPEN_PROBE if probe else Decision.ALLOWED,
                          "executed, ok", 0.0, executed=True, succeeded=True)

    def _emit(self, decision, reason, retry_after, *, executed, succeeded, error=None):
        outcome = Outcome(decision, reason, retry_after, executed, succeeded, error)
        self.history.append(outcome)
        return outcome

    def decisions(self) -> list[Decision]:
        """整段调用的决策序列，用于可复现性断言。"""
        return [o.decision for o in self.history]
