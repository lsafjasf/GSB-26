"""numdiff 自测：与解析导数对拍。

运行: python3 -m unittest discover -s tests -v   （从仓库根目录）
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from numdiff import (
    SCHEMES,
    adaptive_derivative,
    derivative,
    differentiate_grid,
    fornberg_weights,
    optimal_step_theory,
)


class TestFornbergWeights(unittest.TestCase):
    """Fornberg 权重与经典模板系数一致。"""

    def assertWeightsAlmostEqual(self, w, ref, places=12):
        self.assertEqual(len(w), len(ref))
        for a, b in zip(w, ref):
            self.assertAlmostEqual(a, b, places=places)

    def test_central_first(self):
        self.assertWeightsAlmostEqual(
            fornberg_weights([-1, 0, 1], 0, 1), [-0.5, 0.0, 0.5])

    def test_central_second(self):
        self.assertWeightsAlmostEqual(
            fornberg_weights([-1, 0, 1], 0, 2), [1.0, -2.0, 1.0])

    def test_forward_first(self):
        self.assertWeightsAlmostEqual(
            fornberg_weights([0, 1, 2], 0, 1), [-1.5, 2.0, -0.5])

    def test_five_point_first(self):
        self.assertWeightsAlmostEqual(
            fornberg_weights([-2, -1, 0, 1, 2], 0, 1),
            [1 / 12, -2 / 3, 0.0, 2 / 3, -1 / 12])

    def test_nonuniform(self):
        # 非等距节点上对三次多项式精确（3 点一阶公式精确到 2 次）
        nodes = [0.0, 0.3, 1.0]
        w = fornberg_weights(nodes, 0.3, 1)
        f = lambda x: x ** 2
        approx = sum(wi * f(xi) for wi, xi in zip(w, nodes))
        self.assertAlmostEqual(approx, 0.6, places=12)


class TestSchemes(unittest.TestCase):
    """各差分格式的精度阶验证：h 缩小 10 倍，误差应缩小约 10^p 倍。"""

    def check_order(self, scheme, f, df_exact, x0=0.7, hs=(1e-2, 1e-3)):
        m, _, p, _ = SCHEMES[scheme]
        errs = []
        for h in hs:
            val, _ = derivative(f, x0, h, scheme)
            errs.append(abs(val - df_exact(x0)))
        ratio = errs[0] / max(errs[1], 1e-300)
        # 允许 30% 的阶偏差余量
        self.assertGreater(ratio, 10 ** p * 0.7,
                           f"{scheme}: 阶验证失败 ratio={ratio:.1f}, 期望~10^{p}")

    def test_first_derivative_schemes(self):
        f, df = math.sin, math.cos
        for s in ("forward1", "backward1", "central2", "central4",
                  "forward2", "backward2"):
            self.check_order(s, f, df)

    def test_second_derivative_schemes(self):
        f = math.sin
        d2f = lambda x: -math.sin(x)
        # 二阶导的舍入误差 ~ eps/h^2，h 太小会淹没截断误差，
        # 阶验证需在截断主导区间（较大 h）进行
        for s in ("central2_d2", "central4_d2", "forward2_d2", "backward2_d2"):
            self.check_order(s, f, d2f, hs=(1e-1, 1e-2))

    def test_scheme_eval_count(self):
        _, n = derivative(math.sin, 1.0, 1e-3, "central4")
        self.assertEqual(n, 5)


class TestAdaptive(unittest.TestCase):
    """自适应微分与解析导数对拍：实际误差不得超过估计的 10 倍（同量级）。"""

    CASES = [
        ("sin", math.sin, math.cos, lambda x: -math.sin(x), 1.0),
        ("exp", math.exp, math.exp, math.exp, 0.5),
        ("poly", lambda x: x ** 4 - 2 * x ** 2,
         lambda x: 4 * x ** 3 - 4 * x, lambda x: 12 * x ** 2 - 4, 1.3),
        ("log", math.log, lambda x: 1 / x, lambda x: -1 / x ** 2, 2.0),
        ("gauss", lambda x: math.exp(-x * x),
         lambda x: -2 * x * math.exp(-x * x),
         lambda x: (4 * x * x - 2) * math.exp(-x * x), 1.5),
        ("sqrt", math.sqrt, lambda x: 0.5 / math.sqrt(x),
         lambda x: -0.25 * x ** -1.5, 0.01),
    ]

    def test_analytic_agreement(self):
        for name, f, d1, d2, x0 in self.CASES:
            r1 = adaptive_derivative(f, x0, order=1)
            r2 = adaptive_derivative(f, x0, order=2)
            e1 = abs(r1.value - d1(x0))
            e2 = abs(r2.value - d2(x0))
            self.assertLessEqual(e1, 10 * r1.error_est,
                                 f"{name} d1: err={e1:.2e} est={r1.error_est:.2e}")
            self.assertLessEqual(e2, 10 * r2.error_est,
                                 f"{name} d2: err={e2:.2e} est={r2.error_est:.2e}")

    def test_extreme_scales(self):
        # 尺度差异极大的自变量
        for x0 in (1e-8, 1e-4, 1e4, 1e6, 1e9):
            r1 = adaptive_derivative(math.sin, x0, order=1)
            r2 = adaptive_derivative(math.sin, x0, order=2)
            e1 = abs(r1.value - math.cos(x0))
            e2 = abs(r2.value + math.sin(x0))
            self.assertLessEqual(e1, 10 * r1.error_est, f"x0={x0} d1")
            self.assertLessEqual(e2, 10 * r2.error_est, f"x0={x0} d2")

    def test_accuracy_machine_precision(self):
        # 无噪声时：一阶导应达 ~eps^(2/3) ≈ 1e-10，二阶导 ~eps^(1/2) ≈ 1e-7
        r1 = adaptive_derivative(math.sin, 1.0, order=1)
        r2 = adaptive_derivative(math.sin, 1.0, order=2)
        self.assertLess(abs(r1.value - math.cos(1.0)), 1e-9)
        self.assertLess(abs(r2.value + math.sin(1.0)), 1e-6)

    def test_noise_reported_in_estimate(self):
        # 有噪声时估计应反映噪声水平，且仍覆盖实际误差
        import random
        rng = random.Random(42)
        noise = 1e-6

        def noisy_sin(x):
            return math.sin(x) + noise * (2 * rng.random() - 1)

        r1 = adaptive_derivative(noisy_sin, 1.0, order=1, noise=noise)
        r2 = adaptive_derivative(noisy_sin, 1.0, order=2, noise=noise)
        e1 = abs(r1.value - math.cos(1.0))
        e2 = abs(r2.value + math.sin(1.0))
        self.assertLessEqual(e1, 10 * r1.error_est)
        self.assertLessEqual(e2, 10 * r2.error_est)
        # 估计量级应显著大于无噪声情形
        self.assertGreater(r1.error_est, 1e-8)


class TestGrid(unittest.TestCase):
    """网格微分：边界点与非等距采样。"""

    def test_nonuniform_grid_interior(self):
        # 非等距网格内部点精度
        xs = [0.1 * i + 0.02 * (i % 3) for i in range(30)]
        ys = [math.exp(x) for x in xs]
        d = differentiate_grid(xs, ys, m=1, npoints=4)
        for x, approx in zip(xs[3:-3], d[3:-3]):
            self.assertAlmostEqual(approx, math.exp(x), delta=5e-3)

    def test_boundary_points(self):
        # 区间端点自动使用单侧格式，精度不塌
        xs = [i / 50 for i in range(51)]
        ys = [math.sin(x) for x in xs]
        d = differentiate_grid(xs, ys, m=1)
        self.assertAlmostEqual(d[0], math.cos(xs[0]), delta=1e-3)
        self.assertAlmostEqual(d[-1], math.cos(xs[-1]), delta=1e-3)
        d2 = differentiate_grid(xs, ys, m=2)
        self.assertAlmostEqual(d2[0], -math.sin(xs[0]), delta=1e-1)
        self.assertAlmostEqual(d2[-1], -math.sin(xs[-1]), delta=1e-1)

    def test_polynomial_exact(self):
        # 4 点公式对三次多项式精确
        xs = [0.0, 0.3, 0.7, 1.0, 1.6, 2.0]
        ys = [x ** 3 for x in xs]
        d = differentiate_grid(xs, ys, m=1, npoints=4)
        for x, approx in zip(xs, d):
            self.assertAlmostEqual(approx, 3 * x * x, places=8)


class TestTheory(unittest.TestCase):
    def test_optimal_step_scaling(self):
        # h* ~ eps^(1/3)（一阶）与 eps^(1/4)（二阶）
        h1, e1 = optimal_step_theory(1, 1.0, 1.0, 1e-16)
        h2, e2 = optimal_step_theory(2, 1.0, 1.0, 1e-16)
        self.assertAlmostEqual(h1, (3e-16) ** (1 / 3), places=8)
        self.assertAlmostEqual(h2, (48e-16) ** 0.25, places=8)
        # 最优误差量级：eps^(2/3) 与 eps^(1/2)
        self.assertLess(e1, 1e-9)
        self.assertLess(e2, 1e-6)
        self.assertGreater(e1, 1e-12)


if __name__ == "__main__":
    unittest.main()
