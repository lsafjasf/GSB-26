"""registry.py 自测（仅标准库 unittest）。

运行：python3 test_registry.py -v
"""

import unittest

from registry import Registry, State


class FakeClock:
    """可手动推进的单调时钟，注入 Registry。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_registry(results=None, **kwargs):
    """构造注入假时钟与脚本化检查结果的 Registry。
    results: 可迭代 bool，每次探测取下一个；取尽后恒为 True。"""
    clock = FakeClock()
    outcomes = iter(results) if results is not None else None

    def checker(service, instance_id, address):
        if outcomes is None:
            return True
        return next(outcomes, True)

    kwargs.setdefault("clock", clock)
    kwargs.setdefault("health_checker", checker)
    return Registry(**kwargs), clock


def tick(reg, clock, seconds=1.0):
    """推进时间并执行一轮健康检查。"""
    clock.advance(seconds)
    reg.run_checks()


class TestRegisterAndQuery(unittest.TestCase):
    def test_query_immediately_after_register(self):
        reg, _ = make_registry()
        reg.register("svc", "i1", "10.0.0.1:80")
        got = reg.query("svc")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["state"], "healthy")
        self.assertTrue(got[0]["forwardable"])

    def test_query_unknown_service_returns_empty(self):
        reg, _ = make_registry()
        self.assertEqual(reg.query("nope"), [])

    def test_duplicate_register_is_idempotent_and_resets(self):
        reg, clock = make_registry(results=[False])
        reg.register("svc", "i1", "10.0.0.1:80")
        tick(reg, clock)  # 失败一次 -> suspect
        self.assertEqual(reg.query_all("svc")[0]["state"], "suspect")
        # 重复注册同一实例：更新地址、状态重置 healthy、计数清零
        reg.register("svc", "i1", "10.0.0.2:80")
        all_inst = reg.query_all("svc")
        self.assertEqual(len(all_inst), 1)
        self.assertEqual(all_inst[0]["address"], "10.0.0.2:80")
        self.assertEqual(all_inst[0]["state"], "healthy")
        self.assertEqual(all_inst[0]["consecutive_failures"], 0)

    def test_deregister_is_immediate_and_idempotent(self):
        reg, _ = make_registry()
        reg.register("svc", "i1", "10.0.0.1:80")
        self.assertTrue(reg.deregister("svc", "i1"))
        self.assertEqual(reg.query("svc"), [])
        self.assertEqual(reg.query_all("svc"), [])
        self.assertFalse(reg.deregister("svc", "i1"))  # 重复下线返回 False


class TestRemoval(unittest.TestCase):
    def test_single_failure_does_not_remove(self):
        reg, clock = make_registry(results=[False])
        reg.register("svc", "i1", "10.0.0.1:80")
        tick(reg, clock)
        inst = reg.query_all("svc")[0]
        self.assertEqual(inst["state"], "suspect")
        self.assertEqual(len(reg.query("svc")), 1)  # suspect 仍可转发

    def test_consecutive_failures_remove_instance(self):
        reg, clock = make_registry(results=[False, False, False])
        reg.register("svc", "i1", "10.0.0.1:80")
        tick(reg, clock)  # f1 -> suspect
        tick(reg, clock)  # f2 -> suspect
        self.assertEqual(reg.query_all("svc")[0]["state"], "suspect")
        tick(reg, clock)  # f3 达阈值 -> removed
        inst = reg.query_all("svc")[0]
        self.assertEqual(inst["state"], "removed")
        self.assertFalse(inst["forwardable"])
        self.assertEqual(reg.query("svc"), [])  # 明确空结果，不崩溃

    def test_checker_exception_counts_as_failure(self):
        clock = FakeClock()

        def boom(service, instance_id, address):
            raise RuntimeError("connection refused")

        reg = Registry(failure_threshold=2, clock=clock, health_checker=boom)
        reg.register("svc", "i1", "10.0.0.1:80")
        tick(reg, clock)
        tick(reg, clock)
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")

    def test_removed_instance_stays_for_cooldown(self):
        reg, clock = make_registry(results=[False] * 3)
        reg.register("svc", "i1", "10.0.0.1:80")
        for _ in range(3):
            tick(reg, clock)
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")
        # 冷却期（默认 5s）内即使检查器已恢复也不探测、不恢复
        tick(reg, clock, seconds=4.0)
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")
        # 冷却期满后探测成功 -> recovering
        tick(reg, clock, seconds=2.0)
        self.assertEqual(reg.query_all("svc")[0]["state"], "recovering")


class TestFlapping(unittest.TestCase):
    def test_alternating_failure_success_never_removed(self):
        # 失败/成功严格交替 8 轮：永不摘除，可用集合稳定
        results = [False, True] * 8
        reg, clock = make_registry(results=results)
        reg.register("svc", "i1", "10.0.0.1:80")
        for _ in results:
            tick(reg, clock)
            self.assertEqual(len(reg.query("svc")), 1)  # 始终可转发
        inst = reg._instances[("svc", "i1")]
        states = [s.value for _, s in inst.transitions]
        self.assertEqual(set(states), {"healthy", "suspect"})
        self.assertEqual(len(inst.transitions), 16)  # 仅在两态间往返

    def test_two_failures_then_success_never_removed(self):
        # 连续 2 次失败后成功（阈值 3）：差一次即被打断
        results = [False, False, True] * 5
        reg, clock = make_registry(results=results)
        reg.register("svc", "i1", "10.0.0.1:80")
        for _ in results:
            tick(reg, clock)
            self.assertEqual(len(reg.query("svc")), 1)
        inst = reg._instances[("svc", "i1")]
        self.assertEqual(inst.state, State.HEALTHY)
        self.assertNotIn(State.REMOVED, [s for _, s in inst.transitions])


class TestRecovery(unittest.TestCase):
    def test_recovery_requires_consecutive_successes(self):
        # 摘除后冷却 5s；恢复需连续 2 次成功
        results = [False] * 3 + [True, True]
        reg, clock = make_registry(results=results)
        reg.register("svc", "i1", "10.0.0.1:80")
        for _ in range(3):
            tick(reg, clock)
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")
        tick(reg, clock, seconds=5.0)  # 冷却期满，成功 1 -> recovering
        self.assertEqual(reg.query_all("svc")[0]["state"], "recovering")
        self.assertEqual(reg.query("svc"), [])  # recovering 不参与转发
        tick(reg, clock)  # 成功 2 达阈值 -> healthy
        self.assertEqual(reg.query_all("svc")[0]["state"], "healthy")
        self.assertEqual(len(reg.query("svc")), 1)

    def test_failure_during_recovery_goes_back_to_removed(self):
        results = [False] * 3 + [True, False]
        reg, clock = make_registry(results=results)
        reg.register("svc", "i1", "10.0.0.1:80")
        for _ in range(3):
            tick(reg, clock)
        tick(reg, clock, seconds=5.0)  # -> recovering
        tick(reg, clock)  # 恢复期失败 -> 重新 removed
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")
        # 重新起算冷却：4s 后仍不探测
        tick(reg, clock, seconds=4.0)
        self.assertEqual(reg.query_all("svc")[0]["state"], "removed")


class TestEmptyAndAllDown(unittest.TestCase):
    def test_all_instances_down_returns_empty_not_crash(self):
        reg, clock = make_registry(results=[False] * 6)
        reg.register("svc", "i1", "10.0.0.1:80")
        reg.register("svc", "i2", "10.0.0.2:80")
        for _ in range(3):
            tick(reg, clock)
        self.assertEqual(reg.query("svc"), [])  # 全部不可用：明确空结果
        all_inst = reg.query_all("svc")
        self.assertEqual(len(all_inst), 2)  # 运维仍可见全部实例及状态
        self.assertTrue(all(i["state"] == "removed" for i in all_inst))

    def test_query_all_without_service_filter(self):
        reg, _ = make_registry()
        reg.register("svc-a", "i1", "10.0.0.1:80")
        reg.register("svc-b", "i2", "10.0.0.2:80")
        self.assertEqual(len(reg.query_all()), 2)
        self.assertEqual(len(reg.query_all("svc-a")), 1)

    def test_check_interval_throttles_probes(self):
        calls = []

        def checker(service, instance_id, address):
            calls.append(1)
            return True

        clock = FakeClock()
        reg = Registry(clock=clock, health_checker=checker, check_interval=1.0)
        reg.register("svc", "i1", "10.0.0.1:80")
        tick(reg, clock, seconds=1.0)
        tick(reg, clock, seconds=0.5)  # 间隔不足，跳过
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
