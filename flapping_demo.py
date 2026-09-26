"""抖动场景演示：打印状态序列、切换次数与每周期后可转发实例数。

运行：python3 flapping_demo.py
"""

from registry import Registry, State


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def run_scenario(name, results, step=1.0, **registry_kwargs):
    """按 results 脚本逐周期探测，收集状态序列与可转发实例数。"""
    clock = FakeClock()
    outcomes = iter(results)

    def checker(service, instance_id, address):
        return next(outcomes, True)

    reg = Registry(clock=clock, health_checker=checker, **registry_kwargs)
    reg.register("svc", "i1", "10.0.0.1:80")
    inst = reg._instances[("svc", "i1")]

    forwardable_per_step = []
    for _ in results:
        clock.advance(step)
        reg.run_checks()
        forwardable_per_step.append(len(reg.query("svc")))

    sequence = "healthy" + "".join(" -> " + s.value for _, s in inst.transitions)
    print(f"场景：{name}")
    print(f"  探测脚本: {' '.join('OK' if r else 'F' for r in results)}")
    print(f"  状态序列: {sequence}")
    print(f"  状态切换次数: {len(inst.transitions)}")
    print(f"  每周期后可转发实例数: {forwardable_per_step}")
    print()


def main():
    print("默认参数: failure_threshold=3, recovery_threshold=2, "
          "check_interval=1s, removed_cooldown=5s\n")

    run_scenario(
        "1. 失败/成功严格交替 (F,OK)x8 —— 永不摘除，可用集合稳定",
        [False, True] * 8,
    )
    run_scenario(
        "2. 连续 2 次失败后成功 (F,F,OK)x5 —— 差一次到阈值即被打断",
        [False, False, True] * 5,
    )
    # 摘除后需冷却 5s：用 5s 步长推进，使每次探测都在冷却期满后
    run_scenario(
        "3. 真故障 -> 摘除 -> 恢复中再抖动 -> 最终恢复 (F,F,F,OK,F,OK,OK)",
        [False, False, False, True, False, True, True],
        step=5.0,
    )
    run_scenario(
        "4. 对照：无阈值 (failure=1, recovery=1) 下同场景 1 的抖动",
        [False, True] * 8,
        failure_threshold=1,
        recovery_threshold=1,
        removed_cooldown=0.0,
    )


if __name__ == "__main__":
    main()
