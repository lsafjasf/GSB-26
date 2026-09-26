"""令牌桶限流器：配额（容量/速率）+ 等待策略（不阻塞，返回需等待时长）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AcquireResult:
    allowed: bool
    retry_after: float  # 被拒绝时，距下次可放行还需等待的秒数；放行时为 0.0


class TokenBucketRateLimiter:
    """令牌桶。

    - capacity: 桶容量（突发配额）
    - refill_rate: 每秒补充的令牌数（稳态配额）
    等待策略：非阻塞。调用方拿到 retry_after 后自行决定等待或放弃，
    时间由注入时钟驱动，测试无需真实 sleep。
    """

    def __init__(self, clock, capacity: float, refill_rate: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        self._clock = clock
        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)
        self._tokens = float(capacity)
        self._last_refill = clock.now()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> AcquireResult:
        now = self._clock.now()
        self._refill(now)
        if self._tokens >= tokens:
            self._tokens -= tokens
            return AcquireResult(allowed=True, retry_after=0.0)
        deficit = tokens - self._tokens
        return AcquireResult(allowed=False, retry_after=deficit / self._refill_rate)

    @property
    def available_tokens(self) -> float:
        self._refill(self._clock.now())
        return self._tokens
