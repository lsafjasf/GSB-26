"""决策序列测试：限流 + 熔断组合，时间由 ManualClock 注入。"""
import unittest

from callguard import (
    CallGuard, CircuitBreaker, Decision, ManualClock, State,
    TokenBucketRateLimiter,
)

OK = lambda: "ok"  # noqa: E731


def boom():
    raise RuntimeError("downstream is broken")


def make_guard(clock, *, capacity=5, rate=1.0, threshold=0.5, window=10,
               minimum=4, open_duration=10.0, probes=1, successes_to_close=1):
    limiter = TokenBucketRateLimiter(clock, capacity=capacity, refill_rate=rate)
    breaker = CircuitBreaker(
        clock, failure_rate_threshold=threshold, window_size=window,
        minimum_calls=minimum, open_duration=open_duration,
        half_open_max_probes=probes, half_open_successes_to_close=successes_to_close)
    return CallGuard(limiter, breaker)


class TestRateLimiter(unittest.TestCase):
    def test_burst_then_limited_with_retry_after(self):
        clock = ManualClock()
        limiter = TokenBucketRateLimiter(clock, capacity=2, refill_rate=1.0)
        self.assertTrue(limiter.try_acquire().allowed)
        self.assertTrue(limiter.try_acquire().allowed)
        denied = limiter.try_acquire()
        self.assertFalse(denied.allowed)
        self.assertAlmostEqual(denied.retry_after, 1.0)
        clock.advance(1.0)
        self.assertTrue(limiter.try_acquire().allowed)

    def test_tokens_capped_at_capacity_after_long_idle(self):
        clock = ManualClock()
        limiter = TokenBucketRateLimiter(clock, capacity=3, refill_rate=10.0)
        clock.advance(1000.0)  # 空闲再久也不超容量
        self.assertEqual(limiter.available_tokens, 3.0)


class TestCircuitBreaker(unittest.TestCase):
    def test_no_traffic_stays_closed(self):
        clock = ManualClock()
        breaker = CircuitBreaker(clock, minimum_calls=2, open_duration=5.0)
        clock.advance(10_000.0)
        self.assertIs(breaker.state, State.CLOSED)
        self.assertIsNone(breaker.failure_rate)

    def test_sustained_failure_trips_then_half_open_then_recovers(self):
        clock = ManualClock()
        breaker = CircuitBreaker(clock, failure_rate_threshold=0.5,
                                 window_size=4, minimum_calls=4,
                                 open_duration=10.0)
        for _ in range(4):
            breaker.before_call()
            breaker.on_failure()
        self.assertIs(breaker.state, State.OPEN)
        self.assertIs(breaker.before_call().gate.name, "REJECT_OPEN")
        clock.advance(10.0)
        self.assertIs(breaker.before_call().gate.name, "PROBE")
        breaker.on_success()
        self.assertIs(breaker.state, State.CLOSED)

    def test_half_open_failure_reopens_immediately(self):
        clock = ManualClock()
        breaker = CircuitBreaker(clock, window_size=2, minimum_calls=2,
                                 open_duration=5.0)
        breaker.before_call(); breaker.on_failure()
        breaker.before_call(); breaker.on_failure()
        self.assertIs(breaker.state, State.OPEN)
        clock.advance(5.0)
        breaker.before_call()  # probe
        breaker.on_failure()
        self.assertIs(breaker.state, State.OPEN)

    def test_alternating_results_never_trip(self):
        clock = ManualClock()
        breaker = CircuitBreaker(clock, failure_rate_threshold=0.6,
                                 window_size=4, minimum_calls=4)
        for i in range(20):
            breaker.before_call()
            if i % 2 == 0:
                breaker.on_failure()
            else:
                breaker.on_success()
        self.assertIs(breaker.state, State.CLOSED)

    def test_time_jump_skips_cooldown(self):
        clock = ManualClock()
        breaker = CircuitBreaker(clock, window_size=2, minimum_calls=2,
                                 open_duration=30.0)
        breaker.before_call(); breaker.on_failure()
        breaker.before_call(); breaker.on_failure()
        self.assertIs(breaker.state, State.OPEN)
        clock.advance(3600.0)  # 时间跳跃：直接越过冷却期
        self.assertIs(breaker.state, State.HALF_OPEN)


class TestGuardInteraction(unittest.TestCase):
    def test_rate_limited_calls_do_not_count_as_breaker_failures(self):
        """关键测试：限流期间的拒绝不得把熔断器提前打开。"""
        clock = ManualClock()
        guard = make_guard(clock, capacity=1, rate=0.1,  # 桶极易打满
                           threshold=0.5, window=4, minimum=4)
        # 1 次真实失败 + 连续 9 次限流拒绝
        outcomes = [guard.call(boom)] + [guard.call(OK) for _ in range(9)]
        self.assertEqual(outcomes[0].decision, Decision.ALLOWED)
        self.assertTrue(all(o.decision is Decision.RATE_LIMITED for o in outcomes[1:]))
        # 若限流拒绝被计入失败，失败率早已 >= 0.5 触发熔断
        self.assertIs(guard.breaker.state, State.CLOSED)
        self.assertEqual(guard.breaker.failure_rate, 1.0)  # 统计窗口里只有那 1 次真实执行
        self.assertEqual(len(guard.breaker._window), 1)

    def test_decision_order_breaker_before_limiter(self):
        """熔断打开时优先熔断拒绝，不消耗令牌。"""
        clock = ManualClock()
        guard = make_guard(clock, capacity=5, rate=1.0, window=2, minimum=2,
                           open_duration=10.0)
        guard.call(boom)
        guard.call(boom)
        self.assertIs(guard.breaker.state, State.OPEN)
        tokens_before = guard.limiter.available_tokens
        outcome = guard.call(OK)
        self.assertIs(outcome.decision, Decision.CIRCUIT_OPEN)
        self.assertEqual(guard.limiter.available_tokens, tokens_before)

    def test_decision_sequence_is_reproducible(self):
        def run():
            clock = ManualClock()
            guard = make_guard(clock, capacity=10, rate=10.0, window=4,
                               minimum=4, open_duration=5.0)
            script = [OK, OK, boom, boom, boom, boom, OK, OK, OK, OK]
            for fn in script:
                guard.call(fn)
                clock.advance(0.5)
            clock.advance(10.0)
            guard.call(OK)  # 半开试探
            guard.call(OK)
            return guard.decisions()

        first, second = run(), run()
        self.assertEqual(first, second)
        self.assertEqual(
            [d.value for d in first],
            ["allowed", "allowed", "allowed", "allowed",
             "circuit_open", "circuit_open", "circuit_open", "circuit_open",
             "circuit_open", "circuit_open",
             "half_open_probe", "allowed"])

    def test_half_open_probe_slot_exhaustion_and_cancel(self):
        clock = ManualClock()
        guard = make_guard(clock, capacity=5, rate=5.0, window=2, minimum=2,
                           open_duration=5.0, probes=1)
        guard.call(boom)
        guard.call(boom)
        clock.advance(5.0)
        # 占住唯一试探名额但不结束（模拟在途请求）
        gate = guard.breaker.before_call()
        self.assertEqual(gate.gate.name, "PROBE")
        # 第二个并发请求：试探名额已满
        outcome = guard.call(OK)
        self.assertIs(outcome.decision, Decision.HALF_OPEN_BUSY)
        guard.breaker.on_success()  # 在途试探成功，熔断器闭合
        self.assertIs(guard.breaker.state, State.CLOSED)

    def test_probe_rate_limited_releases_slot(self):
        clock = ManualClock()
        guard = make_guard(clock, capacity=2, rate=0.5, window=2, minimum=2,
                           open_duration=5.0, probes=1)
        guard.call(boom)
        guard.call(boom)
        clock.advance(5.0)  # 进入半开，令牌回满到容量 2，先花光
        guard.limiter.try_acquire()
        guard.limiter.try_acquire()
        outcome = guard.call(OK)  # 半开试探被限流拦截
        self.assertIs(outcome.decision, Decision.HALF_OPEN_PROBE)
        self.assertFalse(outcome.executed)
        self.assertEqual(guard.breaker._inflight_probes, 0)  # 名额已归还
        self.assertIs(guard.breaker.state, State.HALF_OPEN)  # 未被误判为失败

    def test_full_recovery_cycle(self):
        clock = ManualClock()
        guard = make_guard(clock, capacity=10, rate=10.0, window=4, minimum=4,
                           open_duration=10.0, successes_to_close=2, probes=1)
        for _ in range(4):
            guard.call(boom)
        self.assertIs(guard.breaker.state, State.OPEN)
        self.assertIs(guard.call(OK).decision, Decision.CIRCUIT_OPEN)
        clock.advance(10.0)
        self.assertIs(guard.call(OK).decision, Decision.HALF_OPEN_PROBE)
        self.assertIs(guard.breaker.state, State.HALF_OPEN)  # 需 2 次成功
        guard.call(OK)
        self.assertIs(guard.breaker.state, State.CLOSED)


if __name__ == "__main__":
    unittest.main()
