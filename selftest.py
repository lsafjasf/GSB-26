"""自测: 边界情形单元测试 + 不同参数组合的估算耗时基准。

运行: python3 selftest.py
"""

from __future__ import annotations

import math
import time
import unittest

from calibrate import calibrate
from capacity_model import capacity_metrics, erlang_c
from recommend import recommend
from simulator import simulate


class EdgeCaseTest(unittest.TestCase):
    def test_zero_traffic(self):
        m = capacity_metrics(0.0, 0.1, 4, timeout=1.0)
        self.assertEqual(m.mean_queue, 0.0)
        self.assertEqual(m.mean_wait, 0.0)
        self.assertEqual(m.p_timeout, 0.0)
        self.assertTrue(m.stable)
        s = simulate(0.0, 0.1, 4, timeout=1.0)
        self.assertEqual(s.served, 0)
        self.assertEqual(s.mean_queue, 0.0)

    def test_single_request(self):
        s = simulate(10.0, 0.1, 4, n_arrivals=1, warmup_frac=0.0)
        self.assertEqual(s.served, 1)
        self.assertEqual(s.mean_wait, 0.0)   # 空系统单请求必无等待
        self.assertEqual(s.max_queue, 0)

    def test_far_beyond_capacity(self):
        # rho = 10, 远超容量
        m = capacity_metrics(100.0, 1.0, 10, timeout=1.0)
        self.assertFalse(m.stable)
        self.assertTrue(math.isinf(m.mean_wait))
        self.assertAlmostEqual(m.p_timeout, 0.9, places=6)  # 流体近似 1-1/rho
        self.assertTrue(m.warnings)
        s = simulate(100.0, 1.0, 10, timeout=1.0, n_arrivals=20000)
        self.assertGreater(s.timeout_prob, 0.5)
        self.assertAlmostEqual(s.utilization, 1.0, delta=0.05)

    def test_invalid_params(self):
        for bad in [dict(lam=-1.0), dict(service_mean=0.0), dict(c=0),
                    dict(cv=-0.5), dict(timeout=-1.0)]:
            kw = dict(lam=1.0, service_mean=0.1, c=2, cv=1.0, timeout=1.0)
            kw.update(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                capacity_metrics(**kw)
            with self.assertRaises(ValueError, msg=str(bad)):
                simulate(kw["lam"], kw["service_mean"], kw["c"], cv=kw["cv"],
                         timeout=kw["timeout"])
        with self.assertRaises(ValueError):
            simulate(1.0, 0.1, 2, n_arrivals=0)
        with self.assertRaises(ValueError):
            simulate(1.0, 0.1, 2, arrival="unknown")
        with self.assertRaises(ValueError):
            recommend(0.0, 0.1)   # 零流量无配额建议

    def test_mm1_known_value(self):
        # M/M/1 精确解: Wq = lam / (mu*(mu-lam)) = 0.8/(1*0.2) = 4s
        m = capacity_metrics(0.8, 1.0, 1, timeout=10.0)
        self.assertAlmostEqual(m.mean_wait, 4.0, places=6)
        self.assertAlmostEqual(m.mean_queue, 3.2, places=6)  # Little: 0.8*4

    def test_erlang_c_bounds(self):
        self.assertEqual(erlang_c(0.0, 5), 0.0)
        self.assertEqual(erlang_c(10.0, 5), 1.0)   # a >= c
        self.assertAlmostEqual(erlang_c(1.0, 1), 1.0)  # M/M/1 rho->1 边界

    def test_mmc_model_vs_sim(self):
        # M/M/c 精确解与模拟应吻合 (15% 内)
        m = capacity_metrics(8.0, 1.0, 10, timeout=2.0)
        s = simulate(8.0, 1.0, 10, timeout=2.0, n_arrivals=30000)
        self.assertLess(abs(m.mean_wait - s.mean_wait) / s.mean_wait, 0.15)
        self.assertLess(abs(m.mean_queue - s.mean_queue) / s.mean_queue, 0.15)

    def test_calibration_runs(self):
        rows, alerts = calibrate(n_arrivals=20000)
        self.assertEqual(len(rows), 4)
        # M/M/c 精确解: 均值类指标应与模拟吻合 (稀有事件 p_timeout 噪声大, 不断言)
        for row in rows:
            if row["name"].startswith("M/M/c"):
                self.assertLess(row["devs"]["mean_wait"], 0.15, row["name"])
                self.assertLess(row["devs"]["mean_queue"], 0.15, row["name"])
        # 突发场景应告警 (模型失效边界的体现)
        self.assertTrue(any("突发" in a for a in alerts))

    def test_recommend_sanity(self):
        q = recommend(100.0, 0.05, cv=1.0, timeout=0.5, peak_factor=2.0)
        self.assertGreaterEqual(q.threads, math.ceil(200 * 0.05 / 0.8))
        self.assertGreater(q.queue, 0)
        self.assertGreater(q.memory_mb, 0.0)
        self.assertLessEqual(q.predicted.p_timeout, 1e-3)
        self.assertTrue(q.rationale and q.uncertainty)


def benchmark() -> None:
    print("\n=== 估算耗时基准 ===")
    print("-- 解析模型 capacity_metrics (lam=0.7*c*mu, mu=10) --")
    print(f"{'c':>6} {'耗时':>10}")
    for c in (1, 10, 100, 1000, 5000):
        t0 = time.perf_counter()
        for _ in range(100):
            capacity_metrics(0.7 * c * 10.0, 0.1, c, timeout=1.0)
        dt = (time.perf_counter() - t0) / 100
        print(f"{c:>6} {dt * 1e6:>8.1f}us")

    print("-- 模拟器 simulate (mu=10, rho=0.8) --")
    print(f"{'c':>6} {'n_arrivals':>12} {'耗时':>10}")
    for c, n in ((4, 10_000), (4, 100_000), (64, 100_000), (256, 100_000),
                 (64, 1_000_000)):
        t0 = time.perf_counter()
        simulate(0.8 * c * 10.0, 0.1, c, timeout=1.0, n_arrivals=n)
        dt = time.perf_counter() - t0
        print(f"{c:>6} {n:>12} {dt * 1000:>8.1f}ms")

    print("-- 配额建议 recommend (rate=100, svc=50ms) --")
    t0 = time.perf_counter()
    recommend(100.0, 0.05, cv=1.5, timeout=0.5, peak_factor=2.0, burst_size=200)
    print(f"{(time.perf_counter() - t0) * 1000:.1f}ms")


if __name__ == "__main__":
    unittest.main(argv=["selftest"], verbosity=2, exit=False)
    benchmark()
