"""服务降级与健康检查状态机（仅标准库，时钟可注入）。

核心概念：
- HealthLevel: 健康等级（完整 / 部分降级 / 最小可用 / 不可用）
- Component: 外部依赖组件，带连续失败/成功阈值做平滑（迟滞）
- HealthMonitor: 汇总各组件状态，计算整体等级，记录审计日志
- FEATURE_MIN_LEVEL: 每个功能允许的最低健康等级
"""

from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum


class HealthLevel(IntEnum):
    """健康等级，数值越大能力越完整。"""

    DOWN = 0      # 不可用：所有关键组件均异常
    MINIMAL = 1   # 最小可用：至少一个关键组件异常，但仍有关键组件可用
    PARTIAL = 2   # 部分降级：关键组件全部正常，存在非关键组件异常
    FULL = 3      # 完整：全部组件正常


LEVEL_NAMES = {
    HealthLevel.FULL: "完整",
    HealthLevel.PARTIAL: "部分降级",
    HealthLevel.MINIMAL: "最小可用",
    HealthLevel.DOWN: "不可用",
}

# 功能门控：每个功能允许运行的最低等级
FEATURE_MIN_LEVEL = {
    "core_query": HealthLevel.MINIMAL,     # 核心查询（只读简化流程）
    "write_order": HealthLevel.PARTIAL,    # 写入/下单（需要全部关键组件）
    "recommend": HealthLevel.FULL,         # 个性化推荐（非关键，完整时才开）
    "cache_warmup": HealthLevel.FULL,      # 缓存预热（非关键）
}


@dataclass
class ComponentConfig:
    name: str
    critical: bool                 # 是否为关键组件
    fail_threshold: int = 3        # 连续失败达到该次数才判定为异常
    recover_threshold: int = 2     # 连续成功达到该次数才判定为恢复
    window_size: int = 20          # 成功率滑动窗口大小


@dataclass
class ComponentState:
    config: ComponentConfig
    healthy: bool = True
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    window: deque = field(default_factory=deque)

    @property
    def success_rate(self) -> float:
        if not self.window:
            return 1.0
        return sum(self.window) / len(self.window)


@dataclass
class AuditRecord:
    """一次等级变化的审计记录。"""

    timestamp: float
    old_level: HealthLevel
    new_level: HealthLevel
    cause_components: list          # 触发本次变化的异常组件名
    success_rates: dict             # 当时各组件的滑动窗口成功率

    def to_dict(self):
        return {
            "timestamp": round(self.timestamp, 3),
            "old_level": LEVEL_NAMES[self.old_level],
            "new_level": LEVEL_NAMES[self.new_level],
            "cause_components": list(self.cause_components),
            "success_rates": {k: round(v, 3) for k, v in self.success_rates.items()},
        }


class HealthMonitor:
    def __init__(self, component_configs, clock):
        """component_configs: List[ComponentConfig]; clock: 可调用对象，返回当前时间戳。"""
        self._clock = clock
        self._components = {c.name: ComponentState(config=c) for c in component_configs}
        self._level = HealthLevel.FULL
        self._audit_log = []
        self._last_unhealthy = set()

    @property
    def level(self) -> HealthLevel:
        return self._level

    @property
    def audit_log(self):
        return list(self._audit_log)

    def success_rates(self):
        return {name: st.success_rate for name, st in self._components.items()}

    def component_healthy(self, name):
        return self._components[name].healthy

    def is_allowed(self, feature: str) -> bool:
        """判断某功能在当前等级下是否允许执行。"""
        return self._level >= FEATURE_MIN_LEVEL[feature]

    def record(self, component_name: str, success: bool):
        """上报一次对某组件的健康检查结果，返回当前整体等级。"""
        st = self._components[component_name]
        cfg = st.config

        st.window.append(success)
        if len(st.window) > cfg.window_size:
            st.window.popleft()

        if success:
            st.consecutive_successes += 1
            st.consecutive_failures = 0
            # 恢复需要连续成功达到阈值（迟滞，防止震荡）
            if not st.healthy and st.consecutive_successes >= cfg.recover_threshold:
                st.healthy = True
        else:
            st.consecutive_failures += 1
            st.consecutive_successes = 0
            # 单次失败不降级，连续失败达到阈值才判定异常
            if st.healthy and st.consecutive_failures >= cfg.fail_threshold:
                st.healthy = False

        self._recompute_level()
        return self._level

    def _recompute_level(self):
        """组合规则：
        - 关键组件全部异常 -> DOWN（需先存在关键组件，否则 0==0 恒成立）
        - 存在关键组件异常（但非全部）-> MINIMAL
        - 关键组件全部正常、存在非关键组件异常 -> PARTIAL
        - 全部正常 -> FULL
        - 没有关键组件时按非关键组件集合计算：全部异常 -> MINIMAL，
          部分异常 -> PARTIAL，全部正常 -> FULL（无关键组件不可能 DOWN）
        """
        unhealthy = {n for n, st in self._components.items() if not st.healthy}
        critical_down = {n for n in unhealthy if self._components[n].config.critical}
        total_critical = sum(1 for st in self._components.values() if st.config.critical)

        if total_critical > 0 and len(critical_down) == total_critical:
            new_level = HealthLevel.DOWN
        elif critical_down:
            new_level = HealthLevel.MINIMAL
        elif total_critical == 0 and unhealthy and len(unhealthy) == len(self._components):
            # 没有关键组件：非关键组件全部异常，最严重只到 MINIMAL
            new_level = HealthLevel.MINIMAL
        elif unhealthy:
            new_level = HealthLevel.PARTIAL
        else:
            new_level = HealthLevel.FULL

        if new_level != self._level:
            # 审计依据：等级下降归因于当前异常组件；等级回升归因于刚恢复的组件
            if new_level < self._level:
                cause = sorted(unhealthy)
            else:
                cause = sorted(self._last_unhealthy - unhealthy) or sorted(unhealthy)
            self._audit_log.append(
                AuditRecord(
                    timestamp=self._clock(),
                    old_level=self._level,
                    new_level=new_level,
                    cause_components=cause,
                    success_rates=self.success_rates(),
                )
            )
            self._level = new_level
        self._last_unhealthy = unhealthy
