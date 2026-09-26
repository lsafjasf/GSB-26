"""test_robust_fit.py -- robust_fit 库的单元测试与边界行为测试。

运行: python3 test_robust_fit.py [-v]
"""

import math
import random
import unittest

from robust_fit import fit, fit_polynomial, poly_design


def make_linear_data(n, slope=3.0, intercept=2.0, noise=0.5, seed=0):
    rng = random.Random(seed)
    x = [10.0 * i / (n - 1) for i in range(n)]
    y = [intercept + slope * xi + rng.gauss(0.0, noise) for xi in x]
    return x, y


class TestOLS(unittest.TestCase):
    def test_recovers_params_on_gaussian_noise(self):
        x, y = make_linear_data(200, seed=1)
        res = fit_polynomial(x, y, 1, method="ols")
        self.assertAlmostEqual(res.params[0], 2.0, delta=0.2)
        self.assertAlmostEqual(res.params[1], 3.0, delta=0.05)
        self.assertGreater(res.r_squared, 0.99)
        self.assertTrue(math.isfinite(res.condition_number))

    def test_quadratic_curve(self):
        rng = random.Random(7)
        x = [i * 0.1 for i in range(100)]
        y = [1.0 - 2.0 * xi + 0.5 * xi * xi + rng.gauss(0, 0.05) for xi in x]
        res = fit_polynomial(x, y, 2, method="ols")
        for got, want in zip(res.params, [1.0, -2.0, 0.5]):
            self.assertAlmostEqual(got, want, delta=0.05)

    def test_single_outlier_breaks_ols_but_not_irls(self):
        x, y = make_linear_data(100, seed=3)
        y[50] += 100.0  # 单个强离群点
        ols = fit_polynomial(x, y, 1, method="ols")
        irls = fit_polynomial(x, y, 1, method="irls")
        self.assertGreater(abs(ols.params[1] - 3.0), abs(irls.params[1] - 3.0))
        self.assertAlmostEqual(irls.params[1], 3.0, delta=0.1)
        self.assertTrue(irls.outliers[50])
        self.assertLess(irls.weights[50], 0.2)

    def test_bisquare_loss(self):
        x, y = make_linear_data(100, seed=4)
        for i in range(0, 100, 10):
            y[i] += 30.0
        res = fit_polynomial(x, y, 1, method="irls", loss="bisquare")
        self.assertAlmostEqual(res.params[1], 3.0, delta=0.1)
        self.assertTrue(res.converged)


class TestEdgeCases(unittest.TestCase):
    def test_perfect_collinearity(self):
        # 第 3 列 = 2 * 第 2 列 -> 秩亏
        rng = random.Random(11)
        x = [rng.uniform(0, 1) for _ in range(50)]
        X = [[1.0, xi, 2.0 * xi] for xi in x]
        y = [1.0 + 3.0 * xi for xi in x]
        res = fit(X, y, method="ols")
        self.assertEqual(res.rank, 2)
        self.assertEqual(res.condition_number, math.inf)
        self.assertTrue(any("rank-deficient" in w for w in res.warnings))
        # 预测仍然正确（可识别部分被解出）
        for xi, yi in zip(x, y):
            pred = res.params[0] + res.params[1] * xi + res.params[2] * 2 * xi
            self.assertAlmostEqual(pred, yi, places=6)

    def test_noiseless_data(self):
        x = [i * 0.5 for i in range(30)]
        y = [2.0 + 3.0 * xi for xi in x]
        for method in ("ols", "irls"):
            res = fit_polynomial(x, y, 1, method=method)
            self.assertAlmostEqual(res.params[0], 2.0, places=8)
            self.assertAlmostEqual(res.params[1], 3.0, places=8)
            self.assertAlmostEqual(res.r_squared, 1.0, places=10)
            self.assertLess(res.residual_stats["max_abs"], 1e-8)
            self.assertTrue(res.converged)

    def test_single_point(self):
        res = fit([[1.0, 2.0]], [5.0], method="ols")
        self.assertEqual(res.rank, 1)
        self.assertTrue(any("underdetermined" in w for w in res.warnings))
        # 残差为 0：单点必被插值
        self.assertAlmostEqual(res.residuals[0], 0.0, places=10)
        irls = fit([[1.0, 2.0]], [5.0], method="irls")
        self.assertAlmostEqual(irls.residuals[0], 0.0, places=8)

    def test_all_outliers(self):
        # 情形 1：同向污染 —— 被截距吸收，拟合收敛但截距偏离真值
        x, y = make_linear_data(60, seed=5)
        y = [yi + 50.0 for yi in y]
        res = fit_polynomial(x, y, 1, method="irls")
        self.assertTrue(res.converged)
        self.assertGreater(res.params[0], 30.0)      # 截距抬升
        self.assertAlmostEqual(res.params[1], 3.0, delta=0.2)  # 斜率仍正确

        # 情形 2：异向污染 —— 超过崩溃点，结果不可信但行为确定：
        # 收敛、参数有限、稳健尺度 sigma 显著变大（诊断信号）
        rng = random.Random(9)
        x, y = make_linear_data(60, seed=9)
        y = [yi + rng.choice([-1.0, 1.0]) * rng.uniform(10.0, 20.0)
             for yi in y]
        res = fit_polynomial(x, y, 1, method="irls")
        self.assertTrue(res.converged)
        self.assertTrue(all(math.isfinite(p) for p in res.params))
        self.assertGreater(res.sigma, 5.0)  # 干净数据 sigma 应约 0.5

    def test_empty_and_mismatched_input(self):
        with self.assertRaises(ValueError):
            fit([], [])
        with self.assertRaises(ValueError):
            fit([[1.0, 1.0]], [1.0, 2.0])
        with self.assertRaises(ValueError):
            fit([[1.0], [1.0, 2.0]], [1.0, 2.0])
        with self.assertRaises(ValueError):
            fit([[1.0]], [1.0], method="nope")
        with self.assertRaises(ValueError):
            fit([[1.0]], [1.0], loss="nope")

    def test_constant_response(self):
        x, _ = make_linear_data(40, seed=6)
        y = [7.5] * 40
        res = fit_polynomial(x, y, 1, method="irls")
        self.assertAlmostEqual(res.params[0], 7.5, places=8)
        self.assertAlmostEqual(res.params[1], 0.0, places=8)


class TestNumerics(unittest.TestCase):
    def test_condition_number_estimate(self):
        # 构造已知病态矩阵：列几乎共线，cond2 应很大
        eps = 1e-8
        X = [[1.0, 1.0], [1.0, 1.0 + eps], [1.0, 1.0 - eps]]
        res = fit(X, [1.0, 2.0, 3.0], method="ols")
        self.assertGreater(res.condition_number, 1e7)

    def test_well_conditioned(self):
        x, y = make_linear_data(50, seed=8)
        res = fit(poly_design(x, 1), y, method="ols")
        self.assertLess(res.condition_number, 20.0)

    def test_no_normal_equations_accuracy(self):
        # 经典反例：直接解正规方程会严重损失精度的病态问题
        x = [1e6 + i for i in range(6)]
        y = [2.0 + 3.0 * xi for xi in x]
        res = fit(poly_design(x, 1), y, method="ols")
        self.assertAlmostEqual(res.params[1], 3.0, delta=1e-4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
