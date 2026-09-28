"""health_fsm 自测：覆盖全部正常、单组件间歇失败、全部失败、快速震荡。

运行：python3 -m unittest test_health_fsm -v
"""

import unittest

from health_fsm import (
    ComponentConfig,
    FEATURE_MIN_LEVEL,
    HealthLevel,
    HealthMonitor,
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt=1.0):
        self.t += dt


def make_monitor(clock, fail_threshold=3, recover_threshold=2):
    """典型部署：db、payment 为关键组件；cache、recommend 为非关键组件。"""
    configs = [
        ComponentConfig("db", critical=True,
                        fail_threshold=fail_threshold,
                        recover_threshold=recover_threshold),
        ComponentConfig("payment", critical=True,
                        fail_threshold=fail_threshold,
                        recover_threshold=recover_threshold),
        ComponentConfig("cache", critical=False,
                        fail_threshold=fail_threshold,
                        recover_threshold=recover_threshold),
        ComponentConfig("recommend", critical=False,
                        fail_threshold=fail_threshold,
                        recover_threshold=recover_threshold),
    ]
    return HealthMonitor(configs, clock)


class TestAllHealthy(unittest.TestCase):
    def test_stays_full(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(10):
            for name in ("db", "payment", "cache", "recommend"):
                clock.advance()
                self.assertEqual(m.record(name, True), HealthLevel.FULL)
        self.assertEqual(m.level, HealthLevel.FULL)
        self.assertEqual(m.audit_log, [])
        for feature in FEATURE_MIN_LEVEL:
            self.assertTrue(m.is_allowed(feature))


class TestSmoothing(unittest.TestCase):
    def test_single_failure_does_not_downgrade(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(2):  # 阈值 3，连续 2 次失败不降级
            clock.advance()
            self.assertEqual(m.record("cache", False), HealthLevel.FULL)
        self.assertTrue(m.component_healthy("cache"))
        self.assertEqual(m.audit_log, [])

    def test_threshold_failure_downgrades_to_partial(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(3):  # 非关键组件连续 3 次失败 -> PARTIAL
            clock.advance()
            level = m.record("cache", False)
        self.assertEqual(level, HealthLevel.PARTIAL)
        self.assertFalse(m.component_healthy("cache"))
        # 部分降级：核心与写路径可用，推荐/预热禁止
        self.assertTrue(m.is_allowed("core_query"))
        self.assertTrue(m.is_allowed("write_order"))
        self.assertFalse(m.is_allowed("recommend"))
        self.assertFalse(m.is_allowed("cache_warmup"))
        # 审计记录
        self.assertEqual(len(m.audit_log), 1)
        rec = m.audit_log[0]
        self.assertEqual(rec.old_level, HealthLevel.FULL)
        self.assertEqual(rec.new_level, HealthLevel.PARTIAL)
        self.assertEqual(rec.cause_components, ["cache"])
        self.assertIn("cache", rec.success_rates)

    def test_recovery_requires_consecutive_successes(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(3):
            m.record("cache", False)
        self.assertEqual(m.level, HealthLevel.PARTIAL)
        # 成功 1 次后又失败：不恢复
        m.record("cache", True)
        self.assertEqual(m.level, HealthLevel.PARTIAL)
        m.record("cache", False)
        m.record("cache", True)
        self.assertEqual(m.level, HealthLevel.PARTIAL)
        # 连续 2 次成功 -> 恢复 FULL
        m.record("cache", True)
        self.assertEqual(m.level, HealthLevel.FULL)
        self.assertEqual(len(m.audit_log), 2)
        rec = m.audit_log[1]
        self.assertEqual(rec.new_level, HealthLevel.FULL)
        self.assertEqual(rec.cause_components, ["cache"])


class TestIntermittentFailure(unittest.TestCase):
    def test_intermittent_failures_never_reach_threshold(self):
        """间歇失败（成败交替）被平滑掉，不触发降级。"""
        clock = FakeClock()
        m = make_monitor(clock)
        pattern = [True, True, False, True, False, True, True, False]
        for _ in range(5):
            for ok in pattern:
                clock.advance()
                self.assertEqual(m.record("recommend", ok), HealthLevel.FULL)
        self.assertEqual(m.audit_log, [])


class TestAllFail(unittest.TestCase):
    def test_all_components_fail_goes_down_then_recovers(self):
        clock = FakeClock()
        m = make_monitor(clock)
        names = ("db", "payment", "cache", "recommend")
        # 全部连续失败 3 轮：先 MINIMAL（关键组件开始异常），再 DOWN
        levels = []
        for _ in range(3):
            for name in names:
                clock.advance()
                levels.append(m.record(name, False))
        self.assertEqual(m.level, HealthLevel.DOWN)
        self.assertIn(HealthLevel.MINIMAL, levels)  # 关键组件部分异常时经过 MINIMAL
        # DOWN：一切功能禁止
        for feature in FEATURE_MIN_LEVEL:
            self.assertFalse(m.is_allowed(feature))
        # 全部连续成功 2 轮 -> 恢复 FULL
        for _ in range(2):
            for name in names:
                clock.advance()
                m.record(name, True)
        self.assertEqual(m.level, HealthLevel.FULL)
        # 审计链完整：FULL->...->DOWN->...->FULL
        seq = [r.new_level for r in m.audit_log]
        self.assertEqual(seq[0], HealthLevel.MINIMAL)
        self.assertEqual(seq[-1], HealthLevel.FULL)
        self.assertIn(HealthLevel.DOWN, seq)


class TestCombination(unittest.TestCase):
    def test_noncritical_only_is_partial(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(3):
            m.record("cache", False)
            m.record("recommend", False)
        self.assertEqual(m.level, HealthLevel.PARTIAL)

    def test_one_critical_down_is_minimal(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(3):
            m.record("db", False)
            m.record("cache", False)
        # 一个关键组件异常（即使非关键也异常）-> MINIMAL
        self.assertEqual(m.level, HealthLevel.MINIMAL)
        self.assertTrue(m.is_allowed("core_query"))
        self.assertFalse(m.is_allowed("write_order"))

    def test_all_critical_down_is_down(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(3):
            m.record("db", False)
            m.record("payment", False)
        self.assertEqual(m.level, HealthLevel.DOWN)


class TestFlapping(unittest.TestCase):
    def test_rapid_flapping_is_damped_by_hysteresis(self):
        """快速震荡：失败刚达阈值又成功，迟滞使等级变化次数远小于故障次数。"""
        clock = FakeClock()
        m = make_monitor(clock)
        # 模式：3 连败（触发降级）-> 2 连胜（触发恢复），重复 5 轮
        for _ in range(5):
            for _ in range(3):
                clock.advance()
                m.record("cache", False)
            for _ in range(2):
                clock.advance()
                m.record("cache", True)
        # 每轮恰好 2 次等级变化（降级+恢复），共 10 次，最终回到 FULL
        self.assertEqual(m.level, HealthLevel.FULL)
        self.assertEqual(len(m.audit_log), 10)
        # 每条审计记录都带时间戳、依据组件与成功率
        for rec in m.audit_log:
            self.assertGreater(rec.timestamp, 0)
            self.assertTrue(rec.cause_components)
            self.assertIn("cache", rec.success_rates)

    def test_sub_threshold_flapping_never_downgrades(self):
        """失败次数始终达不到阈值（2 连败后成功），完全不降级。"""
        clock = FakeClock()
        m = make_monitor(clock)
        for _ in range(10):
            m.record("cache", False)
            m.record("cache", False)
            m.record("cache", True)
        self.assertEqual(m.level, HealthLevel.FULL)
        self.assertEqual(m.audit_log, [])


class TestNoCriticalComponents(unittest.TestCase):
    def make_noncritical_monitor(self, clock):
        """配置中没有任何关键组件：cache、recommend 均为非关键。"""
        configs = [
            ComponentConfig("cache", critical=False),
            ComponentConfig("recommend", critical=False),
        ]
        return HealthMonitor(configs, clock)

    def test_all_healthy_is_full(self):
        # 无关键组件且全部健康：不能因 0==0 恒真比较被判成 DOWN
        clock = FakeClock()
        m = self.make_noncritical_monitor(clock)
        for _ in range(5):
            for name in ("cache", "recommend"):
                self.assertEqual(m.record(name, True), HealthLevel.FULL)
        self.assertEqual(m.level, HealthLevel.FULL)
        self.assertEqual(m.audit_log, [])

    def test_all_down_is_minimal_not_down(self):
        # 无关键组件且全部异常：最严重只到 MINIMAL，不应判为 DOWN
        clock = FakeClock()
        m = self.make_noncritical_monitor(clock)
        for _ in range(3):
            m.record("cache", False)
            m.record("recommend", False)
        self.assertEqual(m.level, HealthLevel.MINIMAL)
        self.assertTrue(m.is_allowed("core_query"))

    def test_partial_failure_is_partial(self):
        clock = FakeClock()
        m = self.make_noncritical_monitor(clock)
        for _ in range(3):
            m.record("cache", False)
        self.assertEqual(m.level, HealthLevel.PARTIAL)


class TestSuccessRateWindow(unittest.TestCase):
    def test_success_rate_uses_sliding_window(self):
        clock = FakeClock()
        m = make_monitor(clock)
        for ok in [True] * 8 + [False] * 2:
            m.record("db", ok)
        self.assertAlmostEqual(m.success_rates()["db"], 0.8)


if __name__ == "__main__":
    unittest.main()
