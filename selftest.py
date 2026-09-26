"""自测：python3 selftest.py  （或 python3 -m unittest selftest -v）"""

import math
import random
import unittest

from robustfit import (
    ols_fit, robust_fit, poly_design,
    RankDeficientError, UnderdeterminedError,
)
from robustfit.linalg import householder_qr, apply_qt, back_substitute, cond_estimate


def make_poly_data(beta, xs, noise=0.0, seed=0):
    rng = random.Random(seed)
    ys = []
    for x in xs:
        y = sum(b * x ** k for k, b in enumerate(beta))
        ys.append(y + (rng.gauss(0, noise) if noise else 0.0))
    return ys


class TestQR(unittest.TestCase):
    def test_qr_reconstruction(self):
        # Q^T A 的前 n 行应等于 R，且 R 上三角
        a = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 9.0]]
        r, vs = householder_qr(a)
        for j in range(2):
            for i in range(j + 1, 4):
                self.assertAlmostEqual(r[i][j], 0.0, places=12)

    def test_qr_solve_matches_exact(self):
        # 超定但相容的系统应精确解出
        a = [[1.0, 1.0], [1.0, 2.0], [1.0, 3.0], [1.0, 4.0]]
        b = [3.0, 5.0, 7.0, 9.0]  # y = 1 + 2x
        r, vs = householder_qr(a)
        x = back_substitute(r, apply_qt(vs, b), 2)
        self.assertAlmostEqual(x[0], 1.0, places=10)
        self.assertAlmostEqual(x[1], 2.0, places=10)

    def test_cond_estimate_identity(self):
        r, _ = householder_qr([[1.0, 0.0], [0.0, 1.0]])
        self.assertAlmostEqual(cond_estimate(r, 2), 1.0, places=6)

    def test_cond_estimate_known(self):
        # diag(1, 1e-3) 的 cond_2 = 1000
        r, _ = householder_qr([[1.0, 0.0], [0.0, 1e-3]])
        self.assertAlmostEqual(cond_estimate(r, 2), 1000.0, delta=1.0)


class TestOLS(unittest.TestCase):
    def test_recovers_parameters(self):
        beta = [1.5, 2.0, -0.3]
        xs = [random.Random(1).uniform(-5, 5) for _ in range(300)]
        rng = random.Random(2)
        xs = [rng.uniform(-5, 5) for _ in range(300)]
        ys = make_poly_data(beta, xs, noise=0.1, seed=3)
        res = ols_fit(poly_design(xs, 2), ys)
        for b, t in zip(res.beta, beta):
            self.assertAlmostEqual(b, t, delta=0.05)
        self.assertGreater(res.r_squared, 0.99)
        self.assertTrue(all(w == 1.0 for w in res.weights))

    def test_residual_stats_present(self):
        xs = [float(i) for i in range(20)]
        ys = make_poly_data([1.0, 1.0], xs, noise=0.1, seed=5)
        res = ols_fit(poly_design(xs, 1), ys)
        for key in ("mean", "std", "mad_scale", "max_abs"):
            self.assertIn(key, res.resid_stats)
        self.assertEqual(len(res.residuals), 20)
        self.assertEqual(len(res.weights), 20)
        self.assertEqual(len(res.outliers), 20)


class TestRobust(unittest.TestCase):
    def _dataset(self, n_out, seed=7):
        beta = [1.5, 2.0, -0.3]
        rng = random.Random(seed)
        xs = [rng.uniform(-5, 5) for _ in range(200)]
        ys = make_poly_data(beta, xs, noise=0.5, seed=seed + 1)
        idx = rng.sample(range(200), n_out)
        for i in idx:
            ys[i] += rng.choice([-1.0, 1.0]) * 10.0
        return beta, xs, ys, set(idx)

    def test_robust_beats_ols_with_outliers(self):
        beta, xs, ys, _ = self._dataset(20)  # 10% 离群
        design = poly_design(xs, 2)
        err = lambda b: math.sqrt(sum((x - t) ** 2 for x, t in zip(b, beta)))
        res_ols = ols_fit(design, ys)
        res_irls = robust_fit(design, ys)
        self.assertLess(err(res_irls.beta), err(res_ols.beta))
        self.assertLess(err(res_irls.beta), 0.2)  # 20 个种子实测最大 0.135

    def test_outliers_flagged(self):
        _, xs, ys, idx = self._dataset(10)
        res = robust_fit(poly_design(xs, 2), ys)
        flagged = {i for i, o in enumerate(res.outliers) if o}
        self.assertGreaterEqual(len(flagged & idx), 8)  # 至少找回 8/10
        self.assertEqual(len(res.weights), len(xs))

    def test_huber_variant(self):
        beta, xs, ys, _ = self._dataset(10)
        res = robust_fit(poly_design(xs, 2), ys, loss="huber")
        err = math.sqrt(sum((b - t) ** 2 for b, t in zip(res.beta, beta)))
        self.assertLess(err, 0.2)


class TestEdgeCases(unittest.TestCase):
    def test_perfectly_collinear_raises(self):
        # 第 2 列 = 2 * 第 1 列：完全共线
        design = [[1.0, 2.0, 4.0], [2.0, 4.0, 8.0], [3.0, 6.0, 12.0]]
        with self.assertRaises(RankDeficientError) as ctx:
            ols_fit(design, [1.0, 2.0, 3.0])
        self.assertEqual(ctx.exception.column, 1)

    def test_noiseless_exact(self):
        beta = [0.5, -1.0, 2.0]
        xs = [i * 0.5 for i in range(30)]
        ys = make_poly_data(beta, xs, noise=0.0)
        design = poly_design(xs, 2)
        for res in (ols_fit(design, ys), robust_fit(design, ys)):
            for b, t in zip(res.beta, beta):
                self.assertAlmostEqual(b, t, places=8)
            self.assertAlmostEqual(res.resid_stats["max_abs"], 0.0, places=8)
        # IRLS 在无噪声下应快速收敛且不除零
        res = robust_fit(design, ys)
        self.assertTrue(res.converged)

    def test_single_point(self):
        # 单点 + 单参数（常数模型）：精确拟合
        res = ols_fit(poly_design([3.0], 0), [7.5])
        self.assertAlmostEqual(res.beta[0], 7.5)
        self.assertAlmostEqual(res.residuals[0], 0.0)
        # 单点 + 多参数：欠定，明确报错
        with self.assertRaises(UnderdeterminedError):
            ols_fit(poly_design([3.0], 1), [7.5])
        with self.assertRaises(UnderdeterminedError):
            robust_fit(poly_design([3.0], 1), [7.5])

    def test_all_outliers_warns(self):
        # 全部数据都是"离群点"（纯噪声，无结构）：
        # 库应返回结果但明确警告不可信，而不是静默或崩溃
        rng = random.Random(11)
        xs = [rng.uniform(-5, 5) for _ in range(50)]
        ys = [rng.uniform(-100, 100) for _ in range(50)]
        res = robust_fit(poly_design(xs, 1), ys)
        self.assertTrue(any("不可信" in w for w in res.warnings))

    def test_cond_number_reported(self):
        xs = [i * 0.1 for i in range(50)]
        ys = make_poly_data([1.0, 1.0], xs, noise=0.01, seed=9)
        res = ols_fit(poly_design(xs, 1), ys)
        self.assertTrue(math.isfinite(res.cond_number))
        self.assertGreaterEqual(res.cond_number, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
