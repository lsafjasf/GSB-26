"""健康降级状态机自测（标准库 unittest，时间由 FakeClock 注入）。

覆盖场景：
1. 全部正常
2. 单组件间歇失败（平滑：单次失败不降级，连续失败才降级，连续成功才恢复）
3. 全部失败与恢复（critical 组件硬门槛）
4. 快速震荡（迟滞抑制等级抖动）
另含：多组件组合规则、审计记录内容、功能允许/禁止矩阵。
"""

import unittest

from health_degradation import (
    ComponentSpec,
    HealthLevel,
    HealthMonitor,
    LEVEL_CAPABILITIES,
)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 10.0) -> float:
        self.now += seconds
        return self.now


def build_monitor(clock):
    """典型部署：db 为 critical；cache/search/recommend 按权重计分。"""
    return HealthMonitor(
        components=[
            ComponentSpec("db", critical=True, fail_threshold=3, recover_threshold=2),
            ComponentSpec("cache", weight=0.40, fail_threshold=3, recover_threshold=2),
            ComponentSpec("search", weight=0.35, fail_threshold=3, recover_threshold=2),
            ComponentSpec("recommend", weight=0.25, fail_threshold=3, recover_threshold=2),
        ],
        clock=clock,
        window_size=10,
    )


def run(monitor, clock, component, results):
    """连续上报一串结果，每步推进时钟，返回 [(t, level, rate), ...] 时间线。"""
    timeline = []
    for ok in results:
        t = clock.advance()
        level = monitor.report(component, ok)
        timeline.append((t, level, monitor.success_rate))
    return timeline


class TestAllHealthy(unittest.TestCase):
    """场景 1：全部正常，等级始终 FULL，无审计记录。"""

    def test_stays_full(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        for component in ("db", "cache", "search", "recommend"):
            for _ in range(5):
                clock.advance()
                self.assertIs(mon.report(component, True), HealthLevel.FULL)
        self.assertAlmostEqual(mon.success_rate, 1.0)
        self.assertEqual(mon.audit_log, [])
        self.assertTrue(mon.is_allowed("personalized_recommend"))


class TestIntermittentFailure(unittest.TestCase):
    """场景 2：单组件（recommend, 权重 0.25）间歇失败。"""

    def test_single_failure_does_not_degrade(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 单次失败穿插成功：永远达不到 fail_threshold=3
        timeline = run(mon, clock, "recommend",
                       [False, True, False, True, False, True])
        for _, level, _ in timeline:
            self.assertIs(level, HealthLevel.FULL)
        self.assertTrue(mon.component_up("recommend"))
        self.assertEqual(mon.audit_log, [])

    def test_two_consecutive_failures_still_full(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        timeline = run(mon, clock, "recommend", [False, False])
        self.assertIs(timeline[-1][1], HealthLevel.FULL)
        self.assertTrue(mon.component_up("recommend"))

    def test_degrade_after_threshold_then_recover(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 连续 3 次失败 -> recommend 下线 -> 健康权重 0.75 -> PARTIAL
        timeline = run(mon, clock, "recommend", [False, False, False])
        self.assertIs(timeline[-1][1], HealthLevel.PARTIAL)
        self.assertFalse(mon.component_up("recommend"))
        self.assertFalse(mon.is_allowed("personalized_recommend"))
        self.assertTrue(mon.is_allowed("full_text_search"))

        # 1 次成功不足以恢复（recover_threshold=2）
        timeline = run(mon, clock, "recommend", [True])
        self.assertIs(timeline[-1][1], HealthLevel.PARTIAL)
        # 第 2 次连续成功 -> 恢复 FULL
        timeline = run(mon, clock, "recommend", [True])
        self.assertIs(timeline[-1][1], HealthLevel.FULL)
        self.assertTrue(mon.is_allowed("personalized_recommend"))

        # 审计：恰好 2 条（降级 + 恢复），时间递增
        audit = mon.audit_log
        self.assertEqual(len(audit), 2)
        down, up = audit
        self.assertEqual((down.old_level, down.new_level),
                         (HealthLevel.FULL, HealthLevel.PARTIAL))
        self.assertEqual(down.trigger_component, "recommend")
        self.assertFalse(down.trigger_ok)
        self.assertEqual(down.down_components, ("recommend",))
        self.assertEqual((up.old_level, up.new_level),
                         (HealthLevel.PARTIAL, HealthLevel.FULL))
        self.assertTrue(up.trigger_ok)
        self.assertEqual(up.down_components, ())
        self.assertLess(down.timestamp, up.timestamp)
        # 成功率快照：降级时窗口 3 连败 -> 0.0；恢复时 3 败 2 成 -> 0.4
        self.assertAlmostEqual(down.success_rate, 0.0)
        self.assertAlmostEqual(up.success_rate, 0.4)


class TestTotalFailure(unittest.TestCase):
    """场景 3：全部组件失败 -> UNAVAILABLE；恢复时 critical 组件是硬门槛。"""

    def test_all_down_then_recover(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 所有组件各连续失败 3 次
        for component in ("db", "cache", "search", "recommend"):
            run(mon, clock, component, [False, False, False])
        self.assertIs(mon.level, HealthLevel.UNAVAILABLE)
        self.assertFalse(mon.is_allowed("static_content"))
        self.assertTrue(mon.is_allowed("health_endpoint"))

        # 非 critical 组件全部恢复，但 db 仍下线 -> 仍 UNAVAILABLE（硬门槛）
        for component in ("cache", "search", "recommend"):
            run(mon, clock, component, [True, True])
        self.assertIs(mon.level, HealthLevel.UNAVAILABLE)

        # db 连续 2 次成功 -> 直接回到 FULL
        run(mon, clock, "db", [True, True])
        self.assertIs(mon.level, HealthLevel.FULL)

        # 审计序列：FULL -> ... -> UNAVAILABLE -> FULL，等级严格变化
        levels = [(r.old_level, r.new_level) for r in mon.audit_log]
        self.assertEqual(levels[0][0], HealthLevel.FULL)
        self.assertEqual(levels[-1][1], HealthLevel.FULL)
        for record in mon.audit_log:
            self.assertNotEqual(record.old_level, record.new_level)


class TestRapidFlapping(unittest.TestCase):
    """场景 4：快速震荡被迟滞吸收，等级与组件状态保持稳定。"""

    def test_flapping_suppressed(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 交替成败 40 次：任何方向都凑不齐连续阈值
        run(mon, clock, "search", [False, True] * 20)
        self.assertIs(mon.level, HealthLevel.FULL)
        self.assertTrue(mon.component_up("search"))
        self.assertEqual(mon.audit_log, [])  # 零审计噪音

    def test_flapping_near_threshold(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 失败-失败-成功 循环：连续失败峰值 2 < 阈值 3
        run(mon, clock, "cache", [False, False, True] * 10)
        self.assertIs(mon.level, HealthLevel.FULL)
        self.assertTrue(mon.component_up("cache"))
        self.assertEqual(mon.audit_log, [])


class TestComposition(unittest.TestCase):
    """多组件同时异常时的等级组合规则。"""

    def test_weight_combination(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # recommend(0.25) 下线 -> 0.75 -> PARTIAL
        run(mon, clock, "recommend", [False] * 3)
        self.assertIs(mon.level, HealthLevel.PARTIAL)
        # search(0.35) 也下线 -> 0.40 -> MINIMAL
        run(mon, clock, "search", [False] * 3)
        self.assertIs(mon.level, HealthLevel.MINIMAL)
        self.assertTrue(mon.is_allowed("cached_read"))
        self.assertFalse(mon.is_allowed("full_text_search"))
        # cache(0.40) 也下线 -> 0.0 -> UNAVAILABLE
        run(mon, clock, "cache", [False] * 3)
        self.assertIs(mon.level, HealthLevel.UNAVAILABLE)
        # 审计序列：FULL -> PARTIAL -> MINIMAL -> UNAVAILABLE
        seq = [r.new_level for r in mon.audit_log]
        self.assertEqual(seq, [HealthLevel.PARTIAL, HealthLevel.MINIMAL,
                               HealthLevel.UNAVAILABLE])

    def test_critical_overrides_weight(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        # 即使非 critical 全部健康，db 下线即 UNAVAILABLE
        run(mon, clock, "db", [False] * 3)
        self.assertIs(mon.level, HealthLevel.UNAVAILABLE)
        self.assertTrue(mon.component_up("cache"))
        self.assertTrue(mon.component_up("search"))

    def test_audit_records_component_rates(self):
        clock = FakeClock()
        mon = build_monitor(clock)
        run(mon, clock, "search", [True, False, False, False])
        record = mon.audit_log[-1]
        rates = dict(record.component_rates)
        self.assertIn("search", rates)
        self.assertAlmostEqual(rates["search"], 0.25)  # 1 成 3 败
        self.assertAlmostEqual(rates["db"], 1.0)       # 未检查视为健康


class TestCapabilitiesMatrix(unittest.TestCase):
    """等级定义完整性：每个等级 allow/deny 不重叠，且随等级单调收敛。"""

    def test_allow_deny_disjoint(self):
        for level, caps in LEVEL_CAPABILITIES.items():
            selfFalse = set(caps["allow"]) & set(caps["deny"])
            self.assertEqual(selfFalse, set(), f"{level} allow/deny overlap")

    def test_allow_sets_shrink_with_level(self):
        ordered = [HealthLevel.FULL, HealthLevel.PARTIAL,
                   HealthLevel.MINIMAL, HealthLevel.UNAVAILABLE]
        for higher, lower in zip(ordered, ordered[1:]):
            self.assertTrue(
                set(LEVEL_CAPABILITIES[lower]["allow"])
                < set(LEVEL_CAPABILITIES[higher]["allow"])
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
