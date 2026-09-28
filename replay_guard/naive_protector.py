"""朴素全量记录参照实现：永不清理，用于与滑动窗口实现对拍。

判定规则与 ReplayProtector 完全一致，只是用全量历史回答
"id 是否在窗口内见过"，不删除任何条目。
"""

from __future__ import annotations

from typing import Dict, Hashable, Optional

from replay_protector import (
    Decision,
    REASON_OK,
    REASON_REPLAY,
    REASON_TOO_FAR_FUTURE,
    REASON_TOO_OLD,
)


class NaiveProtector:
    def __init__(self, window_seconds: float, clock_skew_seconds: float) -> None:
        self._window = float(window_seconds)
        self._skew = float(clock_skew_seconds)
        self._seen: Dict[Hashable, float] = {}
        self._max_ts: Optional[float] = None

    def check(
        self, request_id: Hashable, timestamp: float, now: float
    ) -> Decision:
        anchor = now
        if self._max_ts is not None and self._max_ts > anchor:
            anchor = self._max_ts
        if timestamp > anchor + self._skew:
            return Decision(False, REASON_TOO_FAR_FUTURE)
        if self._max_ts is not None and timestamp < self._max_ts - self._window:
            return Decision(False, REASON_TOO_OLD)
        recorded = self._seen.get(request_id)
        if recorded is not None and recorded >= self._max_ts - self._window:
            return Decision(False, REASON_REPLAY)
        self._seen[request_id] = timestamp
        if self._max_ts is None or timestamp > self._max_ts:
            self._max_ts = timestamp
        return Decision(True, REASON_OK)
