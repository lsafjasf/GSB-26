"""周期性计时器单元测试（python3 test_timer.py 或 python3 -m unittest -v）。"""

import threading
import time
import unittest

from ptimer import (
    LatePolicy,
    NoisyVirtualClock,
    PeriodicTimer,
    QuantizedVirtualClock,
    ResumePolicy,
    SystemClock,
    VirtualClock,
)


class Collector:
    def __init__(self):
        self.ticks = []

    def __call__(self, tick):
        self.ticks.append(tick)


class TestNoDrift(unittest.TestCase):
    def test_absolute_grid_no_drift_ideal_clock(self):
        """理想时钟下：每次触发时刻严格等于 origin + n*period。"""
        clock = VirtualClock()
        col = Collector()
        timer = PeriodicTimer(0.1, col, clock=clock)
        timer.run(max_ticks=1000)
        self.assertEqual(len(col.ticks), 1000)
        for i, tick in enumerate(col.ticks, start=1):
            self.assertAlmostEqual(tick.scheduled, i * 0.1, places=9)
            self.assertAlmostEqual(tick.actual, tick.scheduled, places=9)

    def test_no_drift_under_jitter(self):
        """带抖动时钟下：迟到不累积，下一次自动追回网格。"""
        clock = NoisyVirtualClock(jitter=0.01, seed=42)
        col = Collector()
        timer = PeriodicTimer(0.1, col, clock=clock)
        timer.run(max_ticks=10000)
        self.assertEqual(len(col.ticks), 10000)
        # 每次触发都落在自己的计划槽位 ±jitter 内，无长期漂移。
        for tick in col.ticks:
            self.assertLessEqual(abs(tick.lateness), 0.01 + 1e-9)
        # 与"固定睡眠"对照：naive 方案误差随次数线性累积（见 simulate.py）。

    def test_fire_count_matches_schedule(self):
        """无跳跃时触发次数 = floor(elapsed / period)。"""
        clock = VirtualClock()
        col = Collector()
        timer = PeriodicTimer(0.05, col, clock=clock)
        timer.run(max_ticks=200)
        self.assertEqual(col.ticks[-1].scheduled, 200 * 0.05)


class TestQuantization(unittest.TestCase):
    """最小调度精度 = 10ms 的量化时钟，覆盖 period 大于/等于/小于精度。"""

    QUANTUM = 0.010

    def test_period_greater_than_quantum(self):
        """period=100ms > 10ms：正常工作，迟到被下一次自动追回。"""
        clock = QuantizedVirtualClock(self.QUANTUM)
        col = Collector()
        timer = PeriodicTimer(0.1, col, clock=clock)
        timer.run(max_ticks=500)
        self.assertEqual(len(col.ticks), 500)
        for tick in col.ticks:
            self.assertLessEqual(tick.lateness, self.QUANTUM + 1e-12)
            self.assertGreaterEqual(tick.lateness, -1e-12)

    def test_period_equal_to_quantum(self):
        """period=10ms == 10ms：正常工作，无累积漂移。"""
        clock = QuantizedVirtualClock(self.QUANTUM)
        col = Collector()
        timer = PeriodicTimer(0.010, col, clock=clock)
        timer.run(max_ticks=1000)
        self.assertEqual(len(col.ticks), 1000)
        self.assertAlmostEqual(col.ticks[-1].actual, 1000 * 0.010, places=9)

    def test_period_smaller_than_quantum_coalesce(self):
        """period=3ms < 10ms：COALESCE 降级——每精度单位最多触发一次，
        中间过期槽位合并跳过，但节奏与统计保持正确。"""
        clock = QuantizedVirtualClock(self.QUANTUM)
        col = Collector()
        timer = PeriodicTimer(0.003, col, clock=clock, late_policy=LatePolicy.COALESCE)
        timer.run(max_ticks=100)
        self.assertEqual(len(col.ticks), 100)
        self.assertGreater(timer.stats.skipped, 0)
        # 每个触发点都晚于前一个（时钟以 10ms 步进）。
        actuals = [t.actual for t in col.ticks]
        self.assertEqual(actuals, sorted(actuals))

    def test_period_smaller_than_quantum_burst(self):
        """period=3ms < 10ms：BURST 降级——到期槽位成串补发，平均速率保持。"""
        clock = QuantizedVirtualClock(self.QUANTUM)
        col = Collector()
        timer = PeriodicTimer(0.003, col, clock=clock, late_policy=LatePolicy.BURST)
        timer.run(max_ticks=300)
        self.assertEqual(len(col.ticks), 300)
        self.assertEqual(timer.stats.skipped, 0)
        # 平均周期仍应接近 3ms（虚拟时钟总时长 / 触发次数）。
        elapsed = clock.now()
        self.assertAlmostEqual(elapsed / 300, 0.003, delta=0.001)


class TestJumps(unittest.TestCase):
    def test_forward_jump_skips_and_reports(self):
        """向前跳 0.5s（=5 个槽位）：跳过 5 个槽位，报告 JumpEvent，不补发。"""
        clock = VirtualClock()
        col = Collector()
        jumps = []
        timer = PeriodicTimer(
            0.1, col, clock=clock, on_jump=jumps.append, jump_threshold=0.05
        )

        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == 30:
                clock.jump(0.5)

        timer.on_tick = on_tick
        timer.run(max_ticks=60)

        self.assertEqual(len(jumps), 1)
        ev = jumps[0]
        self.assertEqual(ev.direction, "forward")
        self.assertAlmostEqual(ev.delta, 0.5, places=9)
        # 槽位 31..34 被跳过，槽位 35 在跳变时刻到期、按 COALESCE 触发。
        self.assertEqual(ev.missed_slots, 4)
        self.assertEqual(timer.stats.skipped, 4)
        self.assertEqual(timer.stats.forward_jumps, 1)
        # 跳变后仍触发满 60 次，且没有重复槽位。
        self.assertEqual(len(col.ticks), 60)
        idx = [t.index for t in col.ticks]
        self.assertEqual(len(idx), len(set(idx)))

    def test_forward_jump_burst_policy(self):
        """向前跳 + BURST：错过的槽位成串补发，不丢失。"""
        clock = VirtualClock()
        col = Collector()
        timer = PeriodicTimer(
            0.1, col, clock=clock, late_policy=LatePolicy.BURST, jump_threshold=0.05
        )
        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == 30:
                clock.jump(0.5)

        timer.on_tick = on_tick
        timer.run(max_ticks=65)
        self.assertEqual(timer.stats.skipped, 0)
        self.assertEqual(len(col.ticks), 65)

    def test_backward_jump_no_duplicate_no_stall(self):
        """回拨 5s：不重复触发已触发槽位，且最多多等一个周期（无长停顿）。"""
        clock = VirtualClock()
        col = Collector()
        jumps = []
        sleeps = []
        orig_sleep = clock.sleep

        def spy_sleep(seconds):
            sleeps.append(seconds)
            orig_sleep(seconds)

        clock.sleep = spy_sleep
        timer = PeriodicTimer(
            0.1, col, clock=clock, on_jump=jumps.append, jump_threshold=0.05
        )
        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == 30:
                clock.jump(-5.0)

        timer.on_tick = on_tick
        timer.run(max_ticks=60)

        self.assertEqual(len(jumps), 1)
        ev = jumps[0]
        self.assertEqual(ev.direction, "backward")
        self.assertAlmostEqual(ev.delta, -5.0, places=9)
        self.assertTrue(ev.reanchored)
        self.assertEqual(timer.stats.backward_jumps, 1)
        # 不重复：槽位序号单调递增。
        idx = [t.index for t in col.ticks]
        self.assertEqual(idx, sorted(idx))
        self.assertEqual(len(idx), len(set(idx)))
        # 无长停顿：回拨后的单次睡眠不超过一个周期。
        after_jump = sleeps[31:]
        self.assertTrue(after_jump)
        self.assertLessEqual(max(after_jump), 0.1 + 1e-9)

    def test_small_backward_step_below_threshold(self):
        """小幅回拨（< 阈值）不触发重锚，停顿有界（<= 2 个周期）。"""
        clock = VirtualClock()
        col = Collector()
        jumps = []
        sleeps = []
        orig_sleep = clock.sleep

        def spy_sleep(seconds):
            sleeps.append(seconds)
            orig_sleep(seconds)

        clock.sleep = spy_sleep
        timer = PeriodicTimer(
            0.1, col, clock=clock, on_jump=jumps.append, jump_threshold=0.05
        )
        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == 20:
                clock.jump(-0.03)

        timer.on_tick = on_tick
        timer.run(max_ticks=40)
        self.assertEqual(jumps, [])
        self.assertEqual(len(col.ticks), 40)
        self.assertLessEqual(max(sleeps), 0.2 + 1e-9)


class TestPauseResume(unittest.TestCase):
    def _run_with_pause(self, resume_policy, pause_after=30, pause_len=0.5, max_ticks=60):
        clock = VirtualClock()
        col = Collector()
        timer = PeriodicTimer(
            0.1, col, clock=clock, resume_policy=resume_policy
        )
        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == pause_after:
                timer.pause()

        timer.on_tick = on_tick
        thread = threading.Thread(target=timer.run, kwargs={"max_ticks": max_ticks})
        thread.start()
        self.assertTrue(timer.wait_paused(2.0))
        clock.advance(pause_len)
        timer.resume()
        thread.join(2.0)
        return timer, col

    def test_resume_skip_policy(self):
        """SKIP（默认）：暂停期间的槽位不补发，恢复后继续按周期触发。"""
        timer, col = self._run_with_pause(ResumePolicy.SKIP)
        self.assertEqual(len(col.ticks), 60)
        self.assertEqual(timer.stats.skipped, 5)  # 0.5s / 0.1s
        # 恢复后间隔仍为一个周期。
        gaps = [
            col.ticks[i + 1].actual - col.ticks[i].actual
            for i in range(31, len(col.ticks) - 1)
        ]
        for gap in gaps:
            self.assertAlmostEqual(gap, 0.1, places=9)

    def test_resume_burst_policy(self):
        """BURST：恢复时一次性补发暂停期间错过的全部槽位。"""
        timer, col = self._run_with_pause(ResumePolicy.BURST, max_ticks=65)
        self.assertEqual(len(col.ticks), 65)  # 60 个正常节奏内 + 5 个补发
        self.assertEqual(timer.stats.skipped, 0)

    def test_pause_without_missed_slots_keeps_phase(self):
        """暂停窗口未跨越槽位时，恢复后保持原网格相位。"""
        clock = VirtualClock()
        col = Collector()
        timer = PeriodicTimer(0.1, col, clock=clock, resume_policy=ResumePolicy.SKIP)
        fired = [0]

        def on_tick(tick):
            col(tick)
            fired[0] += 1
            if fired[0] == 10:
                timer.pause()

        timer.on_tick = on_tick
        thread = threading.Thread(target=timer.run, kwargs={"max_ticks": 20})
        thread.start()
        self.assertTrue(timer.wait_paused(2.0))
        clock.advance(0.04)  # 小于一个周期
        timer.resume()
        thread.join(2.0)
        self.assertEqual(len(col.ticks), 20)
        self.assertEqual(timer.stats.skipped, 0)
        self.assertAlmostEqual(col.ticks[-1].scheduled, 20 * 0.1, places=9)


class TestSystemClockSmoke(unittest.TestCase):
    def test_real_clock_fires(self):
        """真实时钟冒烟测试：100ms 周期跑 5 次。"""
        col = Collector()
        timer = PeriodicTimer(0.1, col)
        timer.start()
        deadline = time.monotonic() + 2.0
        while len(col.ticks) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        timer.stop(2.0)
        self.assertGreaterEqual(len(col.ticks), 5)
        for tick in col.ticks[:5]:
            self.assertLess(tick.lateness, 0.1)

    def test_real_clock_pause_resume(self):
        col = Collector()
        timer = PeriodicTimer(0.05, col)
        timer.start()
        time.sleep(0.3)
        self.assertTrue(timer.pause(1.0))
        count_at_pause = len(col.ticks)
        time.sleep(0.3)
        self.assertEqual(len(col.ticks), count_at_pause)  # 暂停期间无触发
        timer.resume()
        time.sleep(0.3)
        timer.stop(2.0)
        self.assertGreater(len(col.ticks), count_at_pause)


if __name__ == "__main__":
    unittest.main(verbosity=2)
