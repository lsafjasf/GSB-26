"""演示四种场景的状态序列、成功率时间线与审计记录。

运行：python3 demo.py
"""

import json

from health_fsm import ComponentConfig, HealthMonitor, LEVEL_NAMES
from test_health_fsm import FakeClock

COMPONENTS = ("db", "payment", "cache", "recommend")


def make_monitor(clock):
    return HealthMonitor(
        [
            ComponentConfig("db", critical=True),
            ComponentConfig("payment", critical=True),
            ComponentConfig("cache", critical=False),
            ComponentConfig("recommend", critical=False),
        ],
        clock,
    )


def run_scenario(title, script):
    """script: List[(component, success)]，每次检查推进 1 秒。"""
    clock = FakeClock(start=0.0)
    m = make_monitor(clock)
    print(f"\n=== {title} ===")
    print(f"{'t':>4} {'check':<22} {'level':<8} " +
          " ".join(f"{n:>9}" for n in COMPONENTS))
    for comp, ok in script:
        clock.advance()
        level = m.record(comp, ok)
        rates = m.success_rates()
        mark = "ok " if ok else "FAIL"
        print(f"{clock.t:>4.0f} {comp + ':' + mark:<22} {LEVEL_NAMES[level]:<8} " +
              " ".join(f"{rates[n]:>9.2f}" for n in COMPONENTS))
    print("审计记录:")
    for rec in m.audit_log:
        print("  " + json.dumps(rec.to_dict(), ensure_ascii=False))
    if not m.audit_log:
        print("  (无等级变化)")


def fail_n(comp, n):
    return [(comp, False)] * n


def ok_n(comp, n):
    return [(comp, True)] * n


# 场景 1：全部正常
run_scenario("场景1 全部正常", [(c, True) for c in COMPONENTS] * 3)

# 场景 2：单组件（cache，非关键）间歇失败后持续失败再恢复
script = []
for _ in range(2):                      # 间歇失败：达不到阈值
    script += ok_n("cache", 2) + fail_n("cache", 2)
script += fail_n("cache", 3)            # 连续 3 次失败 -> PARTIAL
script += ok_n("cache", 1) + fail_n("cache", 1)  # 恢复被一次失败打断
script += ok_n("cache", 2)              # 连续 2 次成功 -> FULL
run_scenario("场景2 单组件间歇失败（cache）", script)

# 场景 3：全部失败后恢复
script = fail_n("db", 3) + fail_n("payment", 3) + fail_n("cache", 3) + fail_n("recommend", 3)
script += ok_n("db", 2) + ok_n("payment", 2) + ok_n("cache", 2) + ok_n("recommend", 2)
run_scenario("场景3 全部失败后恢复", script)

# 场景 4：快速震荡（3 连败 -> 2 连胜，重复 3 轮）
script = []
for _ in range(3):
    script += fail_n("cache", 3) + ok_n("cache", 2)
run_scenario("场景4 快速震荡（cache）", script)
