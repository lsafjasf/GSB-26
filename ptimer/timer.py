"""周期性计时器库（仅标准库）。

核心思想：每次触发时刻由绝对时间网格 ``origin + n*period`` 决定，
睡眠时间始终等于 "下一个网格点 - 当前时刻"，因此单次调度延迟不会
累积。时间源（Clock）可注入，可用虚拟时钟做确定性长周期仿真与
时间跳跃测试。
"""

from __future__ import annotations

import enum
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol


class LatePolicy(enum.Enum):
    """当一个以上的触发点已经过期时的处理策略。"""

    COALESCE = "coalesce"
    BURST = "burst"


class ResumePolicy(enum.Enum):
    """暂停恢复后，对暂停期间错过触发点的处理策略。"""

    SKIP = "skip"
    BURST = "burst"


@dataclass(frozen=True)
class Tick:
    """一次触发。``lateness`` 为相对计划时刻的延迟（秒，负值=提前）。"""

    index: int
    scheduled: float
    actual: float

    @property
    def lateness(self) -> float:
        return self.actual - self.scheduled


@dataclass(frozen=True)
class JumpEvent:
    """检测到的时间不连续事件。"""

    direction: str          # "forward" 或 "backward"
    delta: float            # 跳跃量（forward>0，backward<0）
    before: float
    after: float
    missed_slots: int       # 被跳过（或已补发）的槽位数
    reanchored: bool        # 是否把时间网格重新对齐到当前时刻


@dataclass
class Stats:
    fired: int = 0
    skipped: int = 0
    forward_jumps: int = 0
    backward_jumps: int = 0
    max_lateness: float = 0.0
    sum_lateness: float = 0.0
    lateness_samples: List[float] = field(default_factory=list)

    @property
    def avg_lateness(self) -> float:
        return self.sum_lateness / self.fired if self.fired else 0.0

    @property
    def p95_lateness(self) -> float:
        if not self.lateness_samples:
            return 0.0
        ordered = sorted(self.lateness_samples)
        k = min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1)
        return ordered[k]


class Clock(Protocol):
    """时间源接口。``sleep`` 必须容忍提前唤醒（spurious wake 无害）。"""

    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """系统单调时钟（time.monotonic），不受墙钟回拨影响。"""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VirtualClock:
    """虚拟时钟：时间只在 ``sleep`` / ``advance`` 时前进，支持时间跳跃。"""

    def __init__(self, start: float = 0.0):
        self._t = float(start)
        self._lock = threading.Lock()
        self._pending_jumps: List[float] = []

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            jump = self._pending_jumps.pop(0) if self._pending_jumps else None
        if jump is not None:
            self._t += jump
        else:
            self._t += seconds

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def jump(self, delta: float) -> None:
        """安排一次瞬时跳变（可为负），在下一次 ``sleep`` 时生效。"""
        with self._lock:
            self._pending_jumps.append(delta)


class QuantizedVirtualClock(VirtualClock):
    """最小调度精度受限：睡眠时间向上取整到 ``quantum`` 的整数倍。"""

    def __init__(self, quantum: float, start: float = 0.0):
        super().__init__(start)
        self.quantum = float(quantum)

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            jump = self._pending_jumps.pop(0) if self._pending_jumps else None
        if jump is not None:
            self._t += jump
            return
        units = math.ceil(seconds / self.quantum - 1e-9)
        self._t += units * self.quantum


class NoisyVirtualClock(VirtualClock):
    """叠加确定性噪声的虚拟时钟，用于仿真真实调度延迟。

    ``jitter`` 为对称抖动（±），``delay`` 为单向额外延迟（0..delay），
    后者更接近真实操作系统调度器"只会晚不会早"的特性。
    """

    def __init__(
        self,
        jitter: float = 0.0,
        delay: float = 0.0,
        seed: int = 1,
        start: float = 0.0,
    ):
        super().__init__(start)
        import random

        self._rng = random.Random(seed)
        self.jitter = float(jitter)
        self.delay = float(delay)

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            jump = self._pending_jumps.pop(0) if self._pending_jumps else None
        if jump is not None:
            self._t += jump
            return
        noise = self._rng.uniform(-self.jitter, self.jitter)
        noise += self._rng.uniform(0.0, self.delay)
        self._t += max(0.0, seconds + noise)


TickCallback = Callable[[Tick], None]
JumpCallback = Callable[[JumpEvent], None]


class PeriodicTimer:
    """按绝对时间网格触发的周期计时器。

    计划槽位为 ``origin + n*period``（n 从 1 开始）。每次睡眠到
    "下一个未触发槽位"，而不是固定睡眠 period，故单次调度延迟不会
    累积；若某次触发迟到，下一次会自动少睡以追回网格。
    """

    def __init__(
        self,
        period: float,
        on_tick: TickCallback,
        clock: Optional[Clock] = None,
        *,
        on_jump: Optional[JumpCallback] = None,
        late_policy: LatePolicy = LatePolicy.COALESCE,
        resume_policy: ResumePolicy = ResumePolicy.SKIP,
        jump_threshold: Optional[float] = None,
        keep_samples: bool = True,
    ):
        if period <= 0:
            raise ValueError("period 必须为正数")
        self.period = float(period)
        self.on_tick = on_tick
        self.on_jump = on_jump
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.late_policy = late_policy
        self.resume_policy = resume_policy
        # 小于该阈值的实测偏差视为普通调度抖动，不判为时间跳跃。
        self.jump_threshold = (
            float(jump_threshold)
            if jump_threshold is not None
            else max(self.period, 0.25)
        )
        self.keep_samples = keep_samples
        self.stats = Stats()

        self._thread: Optional[threading.Thread] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._resume = threading.Event()
        # 可中断睡眠的"唤醒信号"。与 _resume 分离：_resume 表示暂停恢复
        # 语义（生命周期内常置位），_wake 只在每次等待前清除、由 pause/stop
        # 置位，避免等待一个早已置位的事件而立即返回（忙等）。
        self._wake = threading.Event()
        self._wake_lock = threading.Lock()
        self._paused_ack = threading.Event()
        self._paused = False

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def run(self, max_ticks: Optional[int] = None) -> None:
        """在当前线程运行循环（注入虚拟时钟时可确定性运行）。"""
        self._run_loop(max_ticks)

    def start(self) -> None:
        """在守护线程中运行（配合 SystemClock 使用）。"""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("计时器已在运行")
        self._stop.clear()
        self._resume.set()
        self._wake.clear()
        self._paused = False
        self._paused_ack.clear()
        self._thread = threading.Thread(target=self._run_loop, args=(None,), daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        self._resume.set()
        with self._wake_lock:
            self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ #
    # 暂停 / 恢复
    # ------------------------------------------------------------------ #
    def pause(self, timeout: Optional[float] = None) -> bool:
        """请求暂停，阻塞到循环确认进入暂停态（或超时）。

        从计时器自身线程（如回调内）调用时只置标志并立即返回，
        暂停在回调返回后生效，避免自我等待死锁。
        """
        self._paused_ack.clear()
        with self._wake_lock:
            self._paused = True
            self._resume.clear()
            self._wake.set()
        if threading.current_thread() is self._loop_thread:
            return True
        return self._paused_ack.wait(timeout)

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._resume.set()

    def wait_paused(self, timeout: Optional[float] = None) -> bool:
        return self._paused_ack.wait(timeout)

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def _run_loop(self, max_ticks: Optional[int]) -> None:
        self._loop_thread = threading.current_thread()
        clock = self.clock
        period = self.period
        origin = clock.now()
        n = 1                       # 下一个待触发槽位序号
        self._resume.set()

        def scheduled(k: int) -> float:
            return origin + k * period

        def due_slot(now: float) -> int:
            return int(math.floor((now - origin) / period + 1e-6))

        while not self._stop.is_set():
            if max_ticks is not None and self.stats.fired >= max_ticks:
                break

            # ---- 暂停：阻塞到 resume，暂停期间的流逝不计调度延迟 ----
            if self._paused:
                self._paused_ack.set()
                self._resume.wait()
                if self._stop.is_set():
                    break
                now = clock.now()
                due = int(math.floor((now - origin) / period + 1e-6))
                if due >= n:
                    if self.resume_policy is ResumePolicy.BURST:
                        # 补发暂停期间错过的全部槽位，随后恢复正常节奏。
                        for k in range(n, due + 1):
                            self._fire_one(k, origin + k * period, now)
                        n = due + 1
                    else:
                        # SKIP（默认）：暂停窗口内的槽位统计后跳过，永不补发；
                        # 网格平移，使下一槽位落在"恢复后一个周期"（同频新相位）。
                        self.stats.skipped += due - n + 1
                        origin = now - (n - 1) * period
                # 暂停窗口未跨越任何槽位时保持原网格相位不变。
                continue

            before = clock.now()
            wait = scheduled(n) - before
            if wait > 0:
                self._interruptible_sleep(wait)
                if self._stop.is_set():
                    break
            after = clock.now()

            if self._paused:
                continue

            # ---- 时间回拨：实测流逝显著为负 ----
            if after - before < -self.jump_threshold:
                # 重新对齐到当前时刻，下一槽位 = now + period。
                # 不补发、不重复触发，且最多再等一个周期（无长停顿）。
                origin = after - (n - 1) * period
                event = JumpEvent(
                    direction="backward",
                    delta=after - before,
                    before=before,
                    after=after,
                    missed_slots=0,
                    reanchored=True,
                )
                self.stats.backward_jumps += 1
                if self.on_jump:
                    self.on_jump(event)
                continue

            target = scheduled(n)
            lateness = after - target

            # ---- 时间向前跳跃：醒来后越过了多个槽位 ----
            if lateness > self.jump_threshold:
                missed = max(0, due_slot(after) - n)
                event = JumpEvent(
                    direction="forward",
                    delta=after - before,
                    before=before,
                    after=after,
                    missed_slots=missed,
                    reanchored=False,
                )
                self.stats.forward_jumps += 1
                if self.on_jump:
                    self.on_jump(event)

            # ---- 触发已过期槽位（正常迟到 / 前跳共用同一套策略）----
            n = self._fire_due(origin, n, clock.now())

    # ------------------------------------------------------------------ #
    def _interruptible_sleep(self, seconds: float) -> None:
        """真实时钟下可被 pause/stop 提前打断；虚拟时钟直接推进时间。"""
        if isinstance(self.clock, SystemClock):
            deadline = time.monotonic() + seconds
            while True:
                with self._wake_lock:
                    # 持锁复查标志，与 pause/stop 的置位互斥，杜绝
                    # "唤醒信号刚置位就被本循环清掉"的丢唤醒窗口。
                    if self._stop.is_set() or self._paused:
                        return
                    self._wake.clear()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                interrupted = self._wake.wait(remaining)
                if not interrupted:
                    return
                # 被唤醒但标志已解除（如 pause 后立即 resume）：回到循环
                # 复查并继续睡到原 deadline，不提前触发。
        else:
            self.clock.sleep(seconds)

    def _fire_due(self, origin: float, n: int, now: float) -> int:
        """触发所有已过期槽位，按 late_policy 合并或补发；返回新 n。"""
        period = self.period
        due = int(math.floor((now - origin) / period + 1e-6))
        if due < n:
            return n

        if self.late_policy is LatePolicy.BURST:
            for k in range(n, due + 1):
                self._fire_one(k, origin + k * period, now)
            return due + 1

        # COALESCE（默认）：合并为最后一个过期槽位，中间槽位记为跳过。
        skipped = due - n
        if skipped > 0:
            self.stats.skipped += skipped
        self._fire_one(due, origin + due * period, now)
        return due + 1

    def _fire_one(self, index: int, scheduled_at: float, actual: float) -> None:
        lateness = actual - scheduled_at
        self.stats.fired += 1
        self.stats.sum_lateness += lateness
        if lateness > self.stats.max_lateness:
            self.stats.max_lateness = lateness
        if self.keep_samples:
            self.stats.lateness_samples.append(lateness)
        self.on_tick(Tick(index, scheduled_at, actual))
