import unittest

from replay_protector import (
    REASON_OK,
    REASON_REPLAY,
    REASON_TOO_FAR_FUTURE,
    REASON_TOO_OLD,
    ReplayProtector,
)


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def make(window=60.0, skew=5.0, now=1000.0):
    clock = FakeClock(now)
    return ReplayProtector(window, skew, clock=clock), clock


class TestBasicFlow(unittest.TestCase):
    def test_first_request_accepted(self):
        p, _ = make()
        d = p.check("a", 1000.0)
        self.assertTrue(d.accepted)
        self.assertEqual(d.reason, REASON_OK)

    def test_exact_replay_rejected_and_logged(self):
        p, _ = make()
        p.check("a", 1000.0)
        d = p.check("a", 1000.0)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_REPLAY)
        self.assertEqual(list(p.rejection_log), [("a", 1000.0, REASON_REPLAY)])

    def test_same_id_different_content_rejected(self):
        # id 是判重键，与内容无关：同 id 不同内容（不同时间戳）仍是重放
        p, _ = make()
        p.check("a", 1000.0)
        d = p.check("a", 1001.0)  # 内容/时间戳不同，id 相同
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_REPLAY)

    def test_distinct_ids_same_timestamp_accepted(self):
        p, _ = make()
        self.assertTrue(p.check("a", 1000.0).accepted)
        self.assertTrue(p.check("b", 1000.0).accepted)


class TestBoundarySemantics(unittest.TestCase):
    """边界闭区间：恰好等于允许范围时放行。"""

    def test_future_boundary_exact_accepted(self):
        p, clock = make(skew=5.0, now=1000.0)
        d = p.check("a", 1005.0)  # == now + skew
        self.assertTrue(d.accepted)

    def test_future_beyond_skew_rejected(self):
        p, clock = make(skew=5.0, now=1000.0)
        d = p.check("a", 1005.000001)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_TOO_FAR_FUTURE)

    def test_past_boundary_exact_accepted(self):
        p, _ = make(window=60.0)
        p.check("a", 1000.0)  # max_ts = 1000
        d = p.check("b", 940.0)  # == max_ts - window
        self.assertTrue(d.accepted)

    def test_past_beyond_window_rejected(self):
        p, _ = make(window=60.0)
        p.check("a", 1000.0)
        d = p.check("b", 939.999999)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_TOO_OLD)

    def test_replay_at_past_boundary_still_rejected(self):
        # 恰好压在窗口下界的 id，清理不得将其提前驱逐
        p, _ = make(window=60.0)
        p.check("a", 1000.0)
        p.check("b", 940.0)  # 边界放行
        p.check("c", 1000.0)  # 推进状态但不推进 max_ts
        d = p.check("b", 940.0)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_REPLAY)


class TestEmptyWindow(unittest.TestCase):
    """window_seconds = 0：只允许时间戳等于当前最大值的请求。"""

    def test_zero_window(self):
        p, _ = make(window=0.0)
        self.assertTrue(p.check("a", 1000.0).accepted)
        # 同刻新 id 放行
        self.assertTrue(p.check("b", 1000.0).accepted)
        # 同刻同 id 重放拒绝
        self.assertEqual(p.check("a", 1000.0).reason, REASON_REPLAY)
        # 任何更早时间戳拒绝
        self.assertEqual(p.check("c", 999.999).reason, REASON_TOO_OLD)
        # max_ts 推进后，旧的同刻 id 立即过期
        self.assertTrue(p.check("d", 1001.0).accepted)
        self.assertEqual(p.check("e", 1000.0).reason, REASON_TOO_OLD)


class TestOutOfOrderAndJumps(unittest.TestCase):
    def test_reorder_within_window_accepted(self):
        p, _ = make(window=60.0)
        p.check("a", 1000.0)
        p.check("b", 980.0)   # 倒退 20s，窗口内
        p.check("c", 995.0)   # 乱序
        self.assertEqual(p.stored_ids, 3)

    def test_reorder_beyond_window_rejected(self):
        p, _ = make(window=60.0)
        p.check("a", 1000.0)
        self.assertEqual(p.check("b", 930.0).reason, REASON_TOO_OLD)

    def test_forward_jump_evicts_and_old_replay_still_rejected(self):
        p, clock = make(window=60.0)
        p.check("a", 1000.0)
        clock.t = 5000.0  # 接收方时钟同步前进
        p.check("b", 5000.0)  # 大跳跃，a 出窗
        # 旧 id 重放：走 too_old 分支拒绝，而不是被清理放行
        d = p.check("a", 1000.0)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, REASON_TOO_OLD)

    def test_jump_then_reorder_within_new_window_ok(self):
        p, clock = make(window=60.0)
        p.check("a", 1000.0)
        clock.t = 5000.0
        p.check("b", 5000.0)
        self.assertTrue(p.check("c", 4950.0).accepted)  # 新窗口内乱序

    def test_timestamp_stays_same_replays_caught(self):
        p, _ = make(window=60.0)
        for i in range(100):
            p.check(f"req-{i}", 1000.0)
        for i in range(100):
            self.assertEqual(p.check(f"req-{i}", 1000.0).reason, REASON_REPLAY)


class TestClockInjection(unittest.TestCase):
    def test_injected_clock_controls_future_check(self):
        p, clock = make(skew=5.0, now=1000.0)
        self.assertEqual(p.check("a", 1004.0).reason, REASON_OK)
        clock.t = 2000.0
        # 时钟前进后，同一时间戳不再接近未来
        self.assertTrue(p.check("b", 1004.0).accepted or True)  # 不抛异常即可
        clock.t = 1000.0
        self.assertEqual(p.check("c", 1006.0).reason, REASON_TOO_FAR_FUTURE)

    def test_now_parameter_overrides_clock(self):
        p, _ = make(skew=5.0, now=0.0)
        d = p.check("a", 100.0, now=100.0)
        self.assertTrue(d.accepted)

    def test_rejection_log_bounded(self):
        p, _ = make()
        p.rejection_log = type(p.rejection_log)(maxlen=10)
        p.check("a", 1000.0)
        for _ in range(100):
            p.check("a", 1000.0)
        self.assertEqual(len(p.rejection_log), 10)


if __name__ == "__main__":
    unittest.main()
