"""自测：模型正确性、模拟器 sanity、校准告警、配额建议、边界情形。"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capacity_planner.models import (ModelError, erlang_b, erlang_c,
                                     mmc_metrics, mgc_metrics, mmck_metrics,
                                     p_wait_exceeds, burst_overload)
from capacity_planner.simulate import SimConfig, run_simulation
from capacity_planner.calibrate import calibrate
from capacity_planner.recommend import recommend


class TestErlang(unittest.TestCase):
    def test_erlang_b_known(self):
        # c=1: B = a/(1+a)
        self.assertAlmostEqual(erlang_b(1, 0.5), 0.5 / 1.5, places=10)
        # a=0: 无阻塞
        self.assertEqual(erlang_b(5, 0.0), 0.0)

    def test_erlang_c_single_server(self):
        # M/M/1: P(wait) = rho
        self.assertAlmostEqual(erlang_c(1, 0.8), 0.8, places=10)

    def test_mm1_metrics(self):
        # M/M/1: Lq = rho^2/(1-rho), Wq = rho/(mu-lam)
        m = mmc_metrics(lam=8.0, mu=10.0, c=1)
        self.assertAlmostEqual(m.rho, 0.8)
        self.assertAlmostEqual(m.lq, 0.64 / 0.2, places=8)
        self.assertAlmostEqual(m.wq, 0.8 / 2.0, places=8)

    def test_mmc_vs_mm1_consistency(self):
        # c 很大时 P(wait) -> 0
        m = mmc_metrics(lam=10.0, mu=1.0, c=1000)
        self.assertLess(m.p_wait, 1e-6)

    def test_p_wait_exceeds_mm1(self):
        # M/M/1: P(Wq > t) = rho * exp(-(mu-lam)t)
        m = mmc_metrics(lam=8.0, mu=10.0, c=1)
        p = p_wait_exceeds(m, 1, 10.0, 0.5)
        self.assertAlmostEqual(p, 0.8 * math.exp(-2.0 * 0.5), places=10)


class TestModelEdges(unittest.TestCase):
    def test_zero_traffic(self):
        m = mgc_metrics(0.0, 0.1, 1.0, 4)
        self.assertEqual(m.lq, 0.0)
        self.assertEqual(m.wq, 0.0)
        self.assertFalse(m.unstable)

    def test_over_capacity_unstable(self):
        m = mgc_metrics(1000.0, 0.1, 1.0, 4)  # rho = 25
        self.assertTrue(m.unstable)
        self.assertEqual(m.lq, math.inf)

    def test_invalid_params(self):
        with self.assertRaises(ModelError):
            mgc_metrics(-1.0, 0.1, 1.0, 4)
        with self.assertRaises(ModelError):
            mgc_metrics(10.0, 0.0, 1.0, 4)
        with self.assertRaises(ModelError):
            mgc_metrics(10.0, 0.1, -1.0, 4)
        with self.assertRaises(ModelError):
            mgc_metrics(10.0, 0.1, 1.0, 0)
        with self.assertRaises(ModelError):
            mgc_metrics(10.0, 0.1, 1.0, 2.5)
        with self.assertRaises(ModelError):
            mgc_metrics(float("nan"), 0.1, 1.0, 4)
        with self.assertRaises(ModelError):
            mmck_metrics(10.0, 1.0, 4, 3)  # K < c

    def test_mg_c_heavier_tail_longer_wait(self):
        m1 = mgc_metrics(50.0, 0.1, 1.0, 8)
        m2 = mgc_metrics(50.0, 0.1, 3.0, 8)
        self.assertGreater(m2.wq, m1.wq)

    def test_mmck_blocking_decreases_with_k(self):
        b1 = mmck_metrics(10.0, 1.0, 4, 8).p_block
        b2 = mmck_metrics(10.0, 1.0, 4, 32).p_block
        self.assertLess(b2, b1)
        self.assertGreaterEqual(b1, 0.0)

    def test_burst_model(self):
        bm = burst_overload(100, 500, 10.0, 10, 5.0, 1.0)
        self.assertTrue(bm.overload)
        self.assertAlmostEqual(bm.peak_backlog, (500 - 100) * 5.0)
        self.assertGreater(bm.timeout_fraction, 0.0)
        # 未超容量
        bm2 = burst_overload(50, 80, 10.0, 10, 5.0, 1.0)
        self.assertFalse(bm2.overload)


class TestSimulator(unittest.TestCase):
    def test_mm1_matches_theory(self):
        cfg = SimConfig(lam=8.0, c=1, mean_service=0.1, sim_time=20000.0,
                        seed=7)
        r = run_simulation(cfg)
        # 理论 Wq = 0.4s，允许 15% 统计误差
        self.assertAlmostEqual(r.mean_wait, 0.4, delta=0.06)
        self.assertAlmostEqual(r.utilization, 0.8, delta=0.03)

    def test_zero_traffic(self):
        r = run_simulation(SimConfig(lam=0.0, c=4, mean_service=0.1,
                                     sim_time=100.0))
        self.assertEqual(r.arrived, 0)
        self.assertEqual(r.mean_wait, 0.0)

    def test_single_request(self):
        # 极小流量：零星请求，等待为 0
        r = run_simulation(SimConfig(lam=1e-3, c=4, mean_service=0.1,
                                     sim_time=20000.0, seed=1))
        self.assertGreaterEqual(r.arrived, 1)
        self.assertEqual(r.mean_wait, 0.0)

    def test_over_capacity_queue_grows(self):
        r = run_simulation(SimConfig(lam=100.0, c=1, mean_service=0.1,
                                     sim_time=500.0, seed=3))
        self.assertGreater(r.max_queue, 1000)  # 队列持续堆积

    def test_finite_queue_drops(self):
        r = run_simulation(SimConfig(lam=50.0, c=1, mean_service=0.1,
                                     queue_cap=10, sim_time=200.0, seed=5))
        self.assertGreater(r.dropped, 0)
        self.assertLessEqual(r.max_queue, 10)

    def test_lognormal_long_tail(self):
        r = run_simulation(SimConfig(lam=50.0, c=8, mean_service=0.1,
                                     dist="lognormal", scv_service=4.0,
                                     sim_time=2000.0, seed=11))
        self.assertGreater(r.p95_wait, r.mean_wait)

    def test_burst_window(self):
        r = run_simulation(SimConfig(lam=50.0, c=8, mean_service=0.1,
                                     sim_time=1000.0, seed=13,
                                     burst=(500.0, 20.0, 300.0)))
        self.assertGreater(r.max_queue, 50)

    def test_invalid_config(self):
        with self.assertRaises(ModelError):
            run_simulation(SimConfig(lam=-1.0, c=1, mean_service=0.1))
        with self.assertRaises(ModelError):
            run_simulation(SimConfig(lam=1.0, c=0, mean_service=0.1))
        with self.assertRaises(ModelError):
            run_simulation(SimConfig(lam=1.0, c=1, mean_service=0.1,
                                     dist="weibull"))


class TestCalibration(unittest.TestCase):
    def test_mm_c_calibrates_clean(self):
        cfg = SimConfig(lam=40.0, c=8, mean_service=0.1, timeout=0.5,
                        sim_time=5000.0, seed=17)
        rep = calibrate(cfg, threshold=0.15)
        self.assertTrue(rep.ok, msg=rep.format())

    def test_alert_on_unstable(self):
        cfg = SimConfig(lam=200.0, c=8, mean_service=0.1, sim_time=300.0)
        rep = calibrate(cfg)
        self.assertFalse(rep.ok)
        self.assertTrue(rep.alerts)

    def test_alert_on_heavy_tail(self):
        # SCV=8 超出 Allen-Cunneen 可靠范围，应触发告警或至少有说明
        cfg = SimConfig(lam=40.0, c=8, mean_service=0.1, dist="lognormal",
                        scv_service=8.0, sim_time=5000.0, seed=19)
        rep = calibrate(cfg, threshold=0.15)
        self.assertTrue(rep.alerts or rep.notes)


class TestRecommend(unittest.TestCase):
    def test_basic_recommendation(self):
        rec = recommend(lam=100.0, mean_service=0.05, scv_service=1.0,
                        timeout=0.5)
        self.assertTrue(rec.feasible)
        threads = next(i for i in rec.items if i.name == "threads")
        self.assertGreaterEqual(threads.value, math.ceil(100 * 0.05) + 1)
        for item in rec.items:
            self.assertTrue(item.uncertainty)
            self.assertTrue(item.rationale)

    def test_zero_traffic(self):
        rec = recommend(lam=0.0, mean_service=0.05)
        self.assertTrue(rec.feasible)
        threads = next(i for i in rec.items if i.name == "threads")
        self.assertEqual(threads.value, 1)
        self.assertTrue(rec.warnings)

    def test_infeasible_when_far_over_capacity(self):
        rec = recommend(lam=1e6, mean_service=1.0, max_threads=64)
        self.assertFalse(rec.feasible)
        self.assertTrue(rec.warnings)

    def test_burst_increases_queue(self):
        r1 = recommend(lam=100.0, mean_service=0.05, timeout=0.5)
        r2 = recommend(lam=100.0, mean_service=0.05, timeout=0.5,
                       burst=(10.0, 1000.0))
        q1 = next(i for i in r1.items if i.name == "queue").value
        q2 = next(i for i in r2.items if i.name == "queue").value
        self.assertGreater(q2, q1)

    def test_invalid_params(self):
        with self.assertRaises(ModelError):
            recommend(lam=-1.0, mean_service=0.05)
        with self.assertRaises(ModelError):
            recommend(lam=10.0, mean_service=0.0)
        with self.assertRaises(ModelError):
            recommend(lam=10.0, mean_service=0.05, timeout=0.0)
        with self.assertRaises(ModelError):
            recommend(lam=10.0, mean_service=0.05, target_rho=1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
