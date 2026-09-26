"""服务降级与健康检查状态机（仅标准库）。

核心概念：
- HealthLevel: 四级健康等级（完整 / 部分降级 / 最小可用 / 不可用）。
- ComponentSpec: 依赖组件定义，critical 组件是硬门槛，非 critical 组件按权重计分。
- 平滑（迟滞）：连续失败达到 fail_threshold 才判定组件下线；
  连续成功达到 recover_threshold 才判定组件恢复。单次抖动不改变状态。
- 组合规则：任一 critical 组件下线 -> UNAVAILABLE；
  否则按非 critical 组件的健康权重占比映射到等级。
- 审计：整体等级每次变化都会追加一条 AuditRecord，包含时间、
  触发组件、当前下线组件集合与当时成功率。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Deque, Dict, List, Optional, Tuple


class HealthLevel(IntEnum):
    """健康等级，数值越大能力越完整。"""

    UNAVAILABLE = 0  # 不可用：仅保留健康检查端点
    MINIMAL = 1      # 最小可用：静态内容 + 缓存只读
    PARTIAL = 2      # 部分降级：主流程可用，裁剪增强功能
    FULL = 3         # 完整：全部功能


# 每个等级下允许 / 禁止的功能清单
LEVEL_CAPABILITIES: Dict[HealthLevel, Dict[str, Tuple[str, ...]]] = {
    HealthLevel.FULL: {
        "allow": (
            "personalized_recommend",  # 个性化推荐
            "full_text_search",        # 全文检索
            "realtime_write",          # 实时写入
            "cached_read",             # 缓存读取
            "static_content",          # 静态内容
            "health_endpoint",         # 健康检查端点
        ),
        "deny": (),
    },
    HealthLevel.PARTIAL: {
        "allow": (
            "full_text_search",
            "realtime_write",
            "cached_read",
            "static_content",
            "health_endpoint",
        ),
        "deny": (
            "personalized_recommend",  # 裁剪增强功能，保主流程
        ),
    },
    HealthLevel.MINIMAL: {
        "allow": (
            "cached_read",
            "static_content",
            "health_endpoint",
        ),
        "deny": (
            "personalized_recommend",
            "full_text_search",
            "realtime_write",
        ),
    },
    HealthLevel.UNAVAILABLE: {
        "allow": (
            "health_endpoint",  # 仅保留探活，便于外部流量摘除
        ),
        "deny": (
            "personalized_recommend",
            "full_text_search",
            "realtime_write",
            "cached_read",
            "static_content",
        ),
    },
}

# 非 critical 组件健康权重占比 -> 等级的映射阈值（从高到低匹配）
LEVEL_SCORE_THRESHOLDS: Tuple[Tuple[float, HealthLevel], ...] = (
    (1.0, HealthLevel.FULL),
    (0.6, HealthLevel.PARTIAL),
    (0.3, HealthLevel.MINIMAL),
    (0.0, HealthLevel.UNAVAILABLE),
)


@dataclass(frozen=True)
class ComponentSpec:
    """依赖组件定义。

    critical=True 的组件是硬门槛：一旦下线整体直接 UNAVAILABLE。
    非 critical 组件按 weight 参与加权计分。
    """

    name: str
    weight: float = 1.0
    critical: bool = False
    fail_threshold: int = 3     # 连续失败多少次判定下线
    recover_threshold: int = 2  # 连续成功多少次判定恢复


@dataclass(frozen=True)
class AuditRecord:
    """一次整体等级变化的审计记录。"""

    timestamp: float
    old_level: HealthLevel
    new_level: HealthLevel
    trigger_component: str          # 触发本次变化的组件
    trigger_ok: bool                # 该组件本次检查是否成功
    down_components: Tuple[str, ...]  # 变化后处于下线状态的组件
    success_rate: float             # 当时滑动窗口内的整体成功率
    component_rates: Tuple[Tuple[str, float], ...]  # 各组件当时成功率


class _ComponentState:
    """单个组件的运行时状态（带迟滞的上线/下线状态机）。"""

    def __init__(self, spec: ComponentSpec, window_size: int) -> None:
        self.spec = spec
        self.up = True
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.window: Deque[bool] = deque(maxlen=window_size)

    def record(self, ok: bool) -> bool:
        """记录一次检查结果，返回组件 up/down 状态是否发生翻转。"""
        self.window.append(ok)
        flipped = False
        if ok:
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            if not self.up and self.consecutive_successes >= self.spec.recover_threshold:
                self.up = True
                flipped = True
        else:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            if self.up and self.consecutive_failures >= self.spec.fail_threshold:
                self.up = False
                flipped = True
        return flipped

    @property
    def success_rate(self) -> float:
        if not self.window:
            return 1.0
        return sum(1 for ok in self.window if ok) / len(self.window)


class HealthMonitor:
    """健康监控器：汇总各组件检查结果，计算整体等级并记录审计日志。

    clock 可注入，测试中用假时钟；默认 time.monotonic。
    """

    def __init__(
        self,
        components: List[ComponentSpec],
        clock: Callable[[], float] = time.monotonic,
        window_size: int = 20,
    ) -> None:
        if not components:
            raise ValueError("at least one component is required")
        names = [c.name for c in components]
        if len(set(names)) != len(names):
            raise ValueError("component names must be unique")
        self._clock = clock
        self._components: Dict[str, _ComponentState] = {
            c.name: _ComponentState(c, window_size) for c in components
        }
        self._window: Deque[bool] = deque(maxlen=window_size)
        self._level = HealthLevel.FULL
        self._audit: List[AuditRecord] = []

    # ---- 检查上报 ----

    def report(self, component: str, ok: bool) -> HealthLevel:
        """上报一次组件检查结果，返回上报后的整体等级。"""
        state = self._components[component]  # KeyError 即未知组件，快速失败
        self._window.append(ok)
        state.record(ok)
        new_level = self._compute_level()
        if new_level != self._level:
            self._audit.append(
                AuditRecord(
                    timestamp=self._clock(),
                    old_level=self._level,
                    new_level=new_level,
                    trigger_component=component,
                    trigger_ok=ok,
                    down_components=tuple(
                        sorted(n for n, s in self._components.items() if not s.up)
                    ),
                    success_rate=self.success_rate,
                    component_rates=tuple(
                        sorted(
                            (n, s.success_rate)
                            for n, s in self._components.items()
                        )
                    ),
                )
            )
            self._level = new_level
        return self._level

    # ---- 等级计算（组合规则） ----

    def _compute_level(self) -> HealthLevel:
        # 规则 1：任一 critical 组件下线 -> 整体不可用
        for state in self._components.values():
            if state.spec.critical and not state.up:
                return HealthLevel.UNAVAILABLE
        # 规则 2：非 critical 组件按健康权重占比映射等级
        total = sum(
            s.spec.weight for s in self._components.values() if not s.spec.critical
        )
        if total <= 0:
            return HealthLevel.FULL
        healthy = sum(
            s.spec.weight
            for s in self._components.values()
            if not s.spec.critical and s.up
        )
        score = healthy / total
        for threshold, level in LEVEL_SCORE_THRESHOLDS:
            if score >= threshold:
                return level
        return HealthLevel.UNAVAILABLE  # 不可达，防御性返回

    # ---- 查询接口 ----

    @property
    def level(self) -> HealthLevel:
        return self._level

    @property
    def success_rate(self) -> float:
        """滑动窗口内的整体成功率。"""
        if not self._window:
            return 1.0
        return sum(1 for ok in self._window if ok) / len(self._window)

    def component_success_rate(self, component: str) -> float:
        return self._components[component].success_rate

    def component_up(self, component: str) -> bool:
        return self._components[component].up

    def is_allowed(self, feature: str) -> bool:
        return feature in LEVEL_CAPABILITIES[self._level]["allow"]

    @property
    def audit_log(self) -> List[AuditRecord]:
        return list(self._audit)
