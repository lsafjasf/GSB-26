"""服务注册表：静态注册/查询 + 健康检查状态机。

仅使用 Python 3 标准库。时间（clock）与健康检查器（health_checker）均可注入，
便于在测试中精确控制。

状态机（可转发集合 = HEALTHY + SUSPECT）：

    register -> HEALTHY --首次探测失败--> SUSPECT --连续失败达 failure_threshold--> REMOVED
                 ^  ^                     |                                          |
                 |  +--- 任意一次成功 ----+                                    冷却 removed_cooldown
                 |                                                                     后探测成功
                 +--- 连续成功达 recovery_threshold --- RECOVERING <-------------------+
                                                          |
                          REMOVED <------- 任意一次失败 ---+

- HEALTHY    健康：参与转发；每次探测失败进入 SUSPECT。
- SUSPECT    疑似不健康：仍参与转发；成功即回 HEALTHY（失败计数清零），
             连续失败达 failure_threshold 进入 REMOVED。
- REMOVED    已摘除：不参与转发；最短停留 removed_cooldown（期内不探测），
             冷却期满后探测成功进入 RECOVERING，失败则重新起算冷却。
- RECOVERING 恢复中：不参与转发；连续成功达 recovery_threshold 回 HEALTHY，
             任意一次失败直接回 REMOVED 并重新起算冷却。
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


class State(str, Enum):
    HEALTHY = "healthy"
    SUSPECT = "suspect"
    REMOVED = "removed"
    RECOVERING = "recovering"


# 可转发（可用于负载均衡）的状态集合
FORWARDABLE = (State.HEALTHY, State.SUSPECT)


class Instance:
    """单个注册实例及其健康状态。"""

    __slots__ = (
        "service",
        "instance_id",
        "address",
        "state",
        "consecutive_failures",
        "consecutive_successes",
        "removed_since",
        "transitions",
    )

    def __init__(self, service: str, instance_id: str, address: str) -> None:
        self.service = service
        self.instance_id = instance_id
        self.address = address
        self.state = State.HEALTHY
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.removed_since: Optional[float] = None
        self.transitions: List[Tuple[float, State]] = []

    def _enter(self, state: State, now: float) -> None:
        changed = state is not self.state
        self.state = state
        if changed:
            self.transitions.append((now, state))
        if state is State.REMOVED:
            self.removed_since = now
            self.consecutive_failures = 0
            self.consecutive_successes = 0
        elif state is State.HEALTHY:
            self.consecutive_failures = 0
            self.consecutive_successes = 0
        elif state is State.RECOVERING:
            self.consecutive_failures = 0

    def snapshot(self) -> dict:
        return {
            "service": self.service,
            "instance_id": self.instance_id,
            "address": self.address,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "forwardable": self.state in FORWARDABLE,
        }


class Registry:
    """服务注册表。

    参数：
        failure_threshold:  连续失败达到该次数后摘除（默认 3）。
        recovery_threshold: 恢复期中连续成功达到该次数后恢复（默认 2）。
        check_interval:     相邻两次健康检查的最小间隔，秒（默认 1.0）。
        removed_cooldown:   摘除后的最短停留/冷却时长，秒（默认 5.0）。
        clock:              单调时钟，返回秒；默认 time.monotonic，测试可注入。
        health_checker:     callable(service, instance_id, address) -> bool；
                            返回 False 或抛异常均计为一次失败。
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
        check_interval: float = 1.0,
        removed_cooldown: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        health_checker: Optional[Callable[[str, str, str], bool]] = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_threshold < 1:
            raise ValueError("recovery_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        self.check_interval = check_interval
        self.removed_cooldown = removed_cooldown
        self._clock = clock
        self._health_checker = health_checker or (lambda s, i, a: True)
        self._instances: Dict[Tuple[str, str], Instance] = {}
        self._last_check_at: Optional[float] = None

    # ------------------------------------------------------------------ 注册

    def register(self, service: str, instance_id: str, address: str) -> Instance:
        """注册实例。重复注册同一 (service, instance_id) 为幂等操作：
        更新地址、状态重置为 HEALTHY、计数清零（视为运维重新上线）。"""
        key = (service, instance_id)
        inst = self._instances.get(key)
        if inst is None:
            inst = Instance(service, instance_id, address)
            self._instances[key] = inst
        else:
            inst.address = address
            inst._enter(State.HEALTHY, self._clock())
        return inst

    def deregister(self, service: str, instance_id: str) -> bool:
        """实例主动下线，立即移除。幂等：不存在时返回 False。"""
        return self._instances.pop((service, instance_id), None) is not None

    # ------------------------------------------------------------------ 查询

    def query(self, service: str) -> List[dict]:
        """只返回可用于转发的实例（HEALTHY/SUSPECT）。
        服务不存在或全部不可用时返回空列表（明确结果，绝不抛异常）。"""
        return [
            inst.snapshot()
            for (svc, _), inst in self._instances.items()
            if svc == service and inst.state in FORWARDABLE
        ]

    def query_all(self, service: Optional[str] = None) -> List[dict]:
        """返回全部实例及其状态（含 REMOVED/RECOVERING），供运维排查。"""
        return [
            inst.snapshot()
            for (svc, _), inst in self._instances.items()
            if service is None or svc == service
        ]

    # -------------------------------------------------------------- 健康检查

    def run_checks(self) -> None:
        """对全部实例执行一轮健康检查并驱动状态机。
        距上一轮不足 check_interval 时跳过（时间由注入的 clock 判定）。"""
        now = self._clock()
        if (
            self._last_check_at is not None
            and now - self._last_check_at < self.check_interval
        ):
            return
        self._last_check_at = now
        for inst in list(self._instances.values()):
            self._check_one(inst, now)

    def _check_one(self, inst: Instance, now: float) -> None:
        # REMOVED 冷却期内不探测：保证最短停留时长
        if inst.state is State.REMOVED:
            if now - (inst.removed_since or now) < self.removed_cooldown:
                return
        ok = self._probe(inst)
        if ok:
            self._on_success(inst, now)
        else:
            self._on_failure(inst, now)

    def _probe(self, inst: Instance) -> bool:
        try:
            return bool(
                self._health_checker(inst.service, inst.instance_id, inst.address)
            )
        except Exception:
            return False  # 检查器异常按失败处理

    def _on_success(self, inst: Instance, now: float) -> None:
        inst.consecutive_failures = 0
        inst.consecutive_successes += 1
        if inst.state is State.SUSPECT:
            inst._enter(State.HEALTHY, now)  # 任意一次成功即恢复
        elif inst.state is State.REMOVED:
            inst._enter(State.RECOVERING, now)  # 冷却期满后首次成功
        if (
            inst.state is State.RECOVERING
            and inst.consecutive_successes >= self.recovery_threshold
        ):
            inst._enter(State.HEALTHY, now)

    def _on_failure(self, inst: Instance, now: float) -> None:
        inst.consecutive_successes = 0
        inst.consecutive_failures += 1
        if inst.state is State.HEALTHY:
            inst._enter(State.SUSPECT, now)
        if inst.state is State.SUSPECT:
            if inst.consecutive_failures >= self.failure_threshold:
                inst._enter(State.REMOVED, now)
        elif inst.state is State.RECOVERING:
            inst._enter(State.REMOVED, now)  # 恢复期失败：重新摘除并起算冷却
        elif inst.state is State.REMOVED:
            inst._enter(State.REMOVED, now)  # 冷却后仍失败：重新起算冷却
