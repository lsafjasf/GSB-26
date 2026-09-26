"""滑动窗口重放攻击防护库（仅标准库，时间可注入）。

判定语义（边界均为闭区间，恰好等于允许范围时放行）：
  - 未来：timestamp > now + clock_skew_seconds        -> 拒绝 "too_far_future"
        即 timestamp == now + clock_skew_seconds 恰好放行。
  - 过旧：timestamp < max_seen_ts - window_seconds   -> 拒绝 "too_old"
        即 timestamp == max_seen_ts - window_seconds 恰好放行。
  - 重放：request_id 已在窗口内被记录                -> 拒绝 "replay"
        （与请求内容无关，同一 id 即视为重放）
  - 其余放行，并记录 id -> timestamp。

清理不变式：仅驱逐 recorded_ts < max_seen_ts - window_seconds 的条目。
由于放行下界与驱逐下界是同一表达式，任何仍可能被合法放行的 id 都不会被
提前清理，因此清理不会放行重放：被驱逐的 id 若再次到来，其携带的旧时间戳
必然落入 "too_old" 分支而被拒绝。

内存上界：理想情况下存储条目数 == 时间戳落在 [max_seen_ts - window,
max_seen_ts] 区间内的已放行请求数。若请求速率 <= R 条/秒，则条目数
<= R * window_seconds，与请求总量无关（乱序不增加上界，因为乱序请求的
时间戳也必须落在窗口内才能被记录）。
实现采用摊还清理（存量翻倍时才全量驱逐），实际占用 <= 2 倍理想上界
外加常数 1024 条，换取均摊 O(1) 的单次处理耗时。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Hashable, Optional, Tuple

REASON_OK = "ok"
REASON_REPLAY = "replay"
REASON_TOO_OLD = "too_old"
REASON_TOO_FAR_FUTURE = "too_far_future"

_MIN_EVICT_BATCH = 1024


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str


class ReplayProtector:
    def __init__(
        self,
        window_seconds: float,
        clock_skew_seconds: float,
        clock: Callable[[], float] = time.time,
        rejection_log_size: int = 10_000,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("window_seconds must be >= 0")
        if clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must be >= 0")
        self._window = float(window_seconds)
        self._skew = float(clock_skew_seconds)
        self._clock = clock
        self._seen: Dict[Hashable, float] = {}
        self._max_ts: Optional[float] = None
        self._last_evict_size = 0
        # 有界拒绝日志（记录最近的拒绝事件，便于审计；不影响判定内存上界）
        self.rejection_log: Deque[Tuple[Hashable, float, str]] = deque(
            maxlen=rejection_log_size
        )

    @property
    def max_seen_ts(self) -> Optional[float]:
        return self._max_ts

    @property
    def stored_ids(self) -> int:
        return len(self._seen)

    def _too_old_threshold(self) -> float:
        assert self._max_ts is not None
        return self._max_ts - self._window

    def _reject(self, request_id: Hashable, timestamp: float, reason: str) -> Decision:
        self.rejection_log.append((request_id, timestamp, reason))
        return Decision(False, reason)

    def check(
        self,
        request_id: Hashable,
        timestamp: float,
        now: Optional[float] = None,
    ) -> Decision:
        """判定一个请求。now 可显式注入；缺省使用构造时注入的 clock。"""
        if now is None:
            now = self._clock()

        if timestamp > now + self._skew:
            return self._reject(request_id, timestamp, REASON_TOO_FAR_FUTURE)

        if self._max_ts is not None and timestamp < self._too_old_threshold():
            return self._reject(request_id, timestamp, REASON_TOO_OLD)

        # 仅当记录的时间戳仍在窗口内才算重放。真正的重放会携带原时间戳，
        # 若该时间戳仍在窗口内则必然被此处拦截；若已出窗则已被 too_old 拦截。
        # 该比较同时保证摊还清理残留的过期条目不会造成误判。
        recorded = self._seen.get(request_id)
        if recorded is not None and recorded >= self._too_old_threshold():
            return self._reject(request_id, timestamp, REASON_REPLAY)

        self._seen[request_id] = timestamp
        if self._max_ts is None or timestamp > self._max_ts:
            self._max_ts = timestamp
        self._maybe_evict()
        return Decision(True, REASON_OK)

    def _maybe_evict(self) -> None:
        # 摊还清理：存量较上次清理后翻倍时才全量驱逐，均摊 O(1)。
        if len(self._seen) < max(2 * self._last_evict_size, _MIN_EVICT_BATCH):
            return
        threshold = self._too_old_threshold()
        expired = [rid for rid, ts in self._seen.items() if ts < threshold]
        for rid in expired:
            del self._seen[rid]
        self._last_evict_size = len(self._seen)
