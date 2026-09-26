"""故障注入模拟：输出调用量与成功率时间线，展示恢复过程。

场景（60 秒，ManualClock 注入时间，完全可复现）：
  t in [ 0, 10)  下游健康
  t in [10, 40)  下游故障（100% 抛异常）
  t in [40, 60)  下游恢复
流量：每秒 8 次调用尝试；限流 5 req/s（桶容量 10）；
熔断：窗口 10 / 最小 5 次 / 失败率阈值 0.5 / 冷却 10s / 半开每次 1 个试探、2 次成功闭合。

运行：python3 simulation.py  （同时写出 timeline.csv）
"""
import csv

from callguard import CallGuard, CircuitBreaker, ManualClock, TokenBucketRateLimiter

DURATION = 60
ATTEMPTS_PER_SEC = 8
FAULT_START, FAULT_END = 10.0, 40.0


def downstream(clock):
    def call():
        if FAULT_START <= clock.now() < FAULT_END:
            raise RuntimeError("injected fault")
        return "ok"
    return call


def main():
    clock = ManualClock()
    limiter = TokenBucketRateLimiter(clock, capacity=10, refill_rate=5.0)
    breaker = CircuitBreaker(clock, failure_rate_threshold=0.5, window_size=10,
                             minimum_calls=5, open_duration=10.0,
                             half_open_max_probes=1, half_open_successes_to_close=2)
    guard = CallGuard(limiter, breaker)

    rows = []
    for sec in range(DURATION):
        stats = dict.fromkeys(
            ["attempted", "executed", "succeeded", "rate_limited",
             "circuit_open", "half_open_busy", "probe"], 0)
        for i in range(ATTEMPTS_PER_SEC):
            clock.set(sec + i / ATTEMPTS_PER_SEC)
            outcome = guard.call(downstream(clock))
            stats["attempted"] += 1
            key = outcome.decision.value
            if key in stats:
                stats[key] += 1
            if outcome.executed:
                stats["executed"] += 1
                if outcome.decision is not None and "probe" in key:
                    stats["probe"] += 1
                if outcome.succeeded:
                    stats["succeeded"] += 1
        clock.set(sec + 1.0)
        rate = stats["succeeded"] / stats["attempted"] if stats["attempted"] else 0.0
        rows.append((sec, guard.breaker.state.value, *stats.values(), f"{rate:.2f}"))

    header = ["t(s)", "breaker", "attempted", "executed", "succeeded",
              "rate_limited", "circuit_open", "half_open_busy", "probe", "succ_rate"]
    print(("{:>5} {:<10}" + "{:>15}" * 8).format(*header))
    for row in rows:
        print(("{:>5} {:<10}" + "{:>15}" * 8).format(*row))

    with open("timeline.csv", "w", newline="") as fh:
        csv.writer(fh).writerows([header, *rows])
    print("\nwritten: timeline.csv")


if __name__ == "__main__":
    main()
