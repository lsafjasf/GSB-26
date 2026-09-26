"""可注入时钟：生产用 SystemClock，测试用 ManualClock。"""
from __future__ import annotations

import time


class SystemClock:
    """真实时钟，秒为单位（float）。"""

    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """测试时钟，只能显式推进，保证决策序列可复现。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("clock cannot go backwards")
        self._now += seconds
        return self._now

    def set(self, timestamp: float) -> None:
        if timestamp < self._now:
            raise ValueError("clock cannot go backwards")
        self._now = float(timestamp)
