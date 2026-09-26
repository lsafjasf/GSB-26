"""场景演示：打印各场景下成功率与等级的时间线，以及审计记录样例。

运行：python3 demo.py
"""

from health_degradation import ComponentSpec, HealthLevel, HealthMonitor


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 10.0) -> float:
        self.now += seconds
        return self.now


def build_monitor(clock) -> HealthMonitor:
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


def print_timeline(title, steps):
    """steps: [(component, ok), ...]，逐步上报并打印时间线。"""
    clock = FakeClock()
    mon = build_monitor(clock)
    print(f"\n=== {title} ===")
    print(f"{'t(s)':>10} {'component':<10} {'ok':<6} {'rate':>6}  level")
    print(f"{'-':>10} {'(start)':<10} {'-':<6} {'1.00':>6}  {mon.level.name}")
    for component, ok in steps:
        t = clock.advance()
        mon.report(component, ok)
        print(f"{t:>10.0f} {component:<10} {str(ok):<6} "
              f"{mon.success_rate:>6.2f}  {mon.level.name}")
    return mon


def print_audit(mon):
    print("  audit log:")
    if not mon.audit_log:
        print("    (empty - 等级未发生变化)")
    for r in mon.audit_log:
        print(f"    t={r.timestamp:.0f} {r.old_level.name} -> {r.new_level.name}"
              f" trigger={r.trigger_component}(ok={r.trigger_ok})"
              f" down={list(r.down_components)} rate={r.success_rate:.2f}")


def scenario_all_healthy():
    steps = [(c, True) for c in ("db", "cache", "search", "recommend")] * 3
    mon = print_timeline("场景 1: 全部正常", steps)
    print_audit(mon)


def scenario_intermittent():
    # 间歇失败（不降级） -> 连续 3 败（降级 PARTIAL） -> 1 成（不恢复） -> 再 1 成（恢复）
    steps = ([("recommend", False), ("recommend", True)] * 3
             + [("recommend", False)] * 3
             + [("recommend", True), ("recommend", True)])
    mon = print_timeline("场景 2: 单组件间歇失败 (recommend)", steps)
    print_audit(mon)


def scenario_total_failure():
    steps = []
    for c in ("db", "cache", "search", "recommend"):
        steps += [(c, False)] * 3          # 全部打挂
    for c in ("cache", "search", "recommend"):
        steps += [(c, True)] * 2           # 非 critical 先恢复（仍 UNAVAILABLE）
    steps += [("db", True)] * 2            # critical 恢复 -> FULL
    mon = print_timeline("场景 3: 全部失败与恢复", steps)
    print_audit(mon)


def scenario_flapping():
    steps = [("search", ok) for ok in [False, True] * 8]
    mon = print_timeline("场景 4: 快速震荡 (search 成败交替)", steps)
    print_audit(mon)


if __name__ == "__main__":
    scenario_all_healthy()
    scenario_intermittent()
    scenario_total_failure()
    scenario_flapping()
