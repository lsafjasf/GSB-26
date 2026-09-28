"""自测：python3 selftest.py [-v]"""
import math
import unittest

from distfit import (Sampler, StdRandomSource, Uniform, Exponential, Normal,
                     LogNormal, Binomial, Poisson, goodness_of_fit, ks_test,
                     quantile_comparison, quantile_test, fit, fit_best)
from distfit import special


class TestSpecial(unittest.TestCase):
    def test_chi2_ppf_known_values(self):
        # 对照标准卡方表
        self.assertAlmostEqual(special.chi2_ppf(0.95, 1), 3.8415, places=3)
        self.assertAlmostEqual(special.chi2_ppf(0.95, 5), 11.0705, places=3)
        self.assertAlmostEqual(special.chi2_ppf(0.99, 10), 23.2093, places=3)

    def test_normal_ppf_roundtrip(self):
        for p in (1e-6, 0.01, 0.5, 0.99, 1 - 1e-6):
            self.assertAlmostEqual(special.normal_cdf(special.normal_ppf(p)), p,
                                   places=10)


class TestSampling(unittest.TestCase):
    def test_same_seed_same_result(self):
        for make in (lambda: Sampler(StdRandomSource(42)).normal(200, 1, 2),
                     lambda: Sampler(StdRandomSource(42)).exponential(200, 0.5),
                     lambda: Sampler(StdRandomSource(42)).uniform(200, -1, 3),
                     lambda: Sampler(StdRandomSource(42)).binomial(200, 10, 0.3),
                     lambda: Sampler(StdRandomSource(42)).poisson(200, 3.5),
                     lambda: Sampler(StdRandomSource(42)).poisson(200, 500.0),
                     lambda: Sampler(StdRandomSource(42)).lognormal(200, 0.5, 1.5)):
            self.assertEqual(make(), make())

    def test_injected_source_is_used(self):
        class ConstantSource:
            def random(self):
                return 0.5
        s = Sampler(ConstantSource())
        self.assertEqual(s.uniform(3, 2.0, 4.0), [3.0, 3.0, 3.0])
        self.assertAlmostEqual(s.exponential(1, 2.0)[0], math.log(2) / 2.0)
        # 泊松 Knuth 分支：e^-1≈0.368，0.5 > 0.368 >= 0.25 -> 恒为 1
        self.assertEqual(s.poisson(3, 1.0), [1, 1, 1])
        # 对数正态完全由注入源驱动：两个相同常量源结果一致
        self.assertEqual(Sampler(ConstantSource()).lognormal(4, 0.5, 1.5),
                         Sampler(ConstantSource()).lognormal(4, 0.5, 1.5))

    def test_zero_samples(self):
        s = Sampler(StdRandomSource(1))
        self.assertEqual(s.normal(0), [])
        self.assertEqual(s.uniform(0), [])
        with self.assertRaises(ValueError):
            goodness_of_fit([], Normal())
        with self.assertRaises(ValueError):
            fit([], "normal")

    def test_invalid_params(self):
        for bad in (lambda: Uniform(1, 1), lambda: Uniform(2, 1),
                    lambda: Exponential(0), lambda: Exponential(-1),
                    lambda: Normal(0, 0), lambda: Normal(0, -2),
                    lambda: LogNormal(0, 0), lambda: LogNormal(0, -1),
                    lambda: LogNormal(float("inf"), 1),
                    lambda: Poisson(-1), lambda: Poisson(float("inf")),
                    lambda: Poisson(float("nan")),
                    lambda: Binomial(-1, 0.5), lambda: Binomial(5, 1.5),
                    lambda: Binomial(2.5, 0.5)):
            with self.assertRaises(ValueError):
                bad()
        with self.assertRaises(ValueError):
            Sampler(StdRandomSource(1)).normal(-3)

    def test_binomial_support(self):
        s = Sampler(StdRandomSource(7))
        draws = s.binomial(1000, 8, 0.25)
        self.assertTrue(all(isinstance(x, int) and 0 <= x <= 8 for x in draws))


class TestNewDistributionBoundaries(unittest.TestCase):
    def test_poisson_zero_lam_is_degenerate(self):
        # 零方差退化：Poisson(0) 采样恒为 0，pmf/cdf 集中于 0
        d = Poisson(0)
        self.assertEqual(Sampler(StdRandomSource(1)).draw(d, 5), [0] * 5)
        self.assertEqual(d.pmf(0), 1.0)
        self.assertEqual(d.pmf(1), 0.0)
        self.assertEqual(d.cdf(0), 1.0)
        self.assertEqual(list(d.support()), [0])

    def test_poisson_extreme_lam_ptrs_branch(self):
        # 极端大 lam 走 PTRS 分支，均值与方差仍应约等于 lam
        draws = Sampler(StdRandomSource(2)).poisson(20000, 1000.0)
        mean = sum(draws) / len(draws)
        var = sum((x - mean) ** 2 for x in draws) / len(draws)
        self.assertAlmostEqual(mean, 1000.0, delta=3.0)
        self.assertAlmostEqual(var, 1000.0, delta=60.0)
        self.assertTrue(all(isinstance(x, int) and x >= 0 for x in draws))

    def test_poisson_pmf_sums_to_one(self):
        for lam in (0.3, 4.0, 25.0):
            total = sum(Poisson(lam).pmf(k) for k in range(0, 200))
            self.assertAlmostEqual(total, 1.0, places=10)

    def test_lognormal_extreme_params(self):
        # sigma 极小：样本集中在 exp(mu) 附近（近退化但合法）
        draws = Sampler(StdRandomSource(3)).lognormal(1000, 2.0, 1e-3)
        center = math.exp(2.0)
        self.assertTrue(all(abs(x - center) < 0.02 * center for x in draws))
        # sigma 很大：样本仍为正有限值，且跨度极大
        draws = Sampler(StdRandomSource(4)).lognormal(5000, 0.0, 3.0)
        self.assertTrue(all(x > 0 and math.isfinite(x) for x in draws))
        self.assertGreater(max(draws) / min(draws), 1e6)

    def test_lognormal_cdf_ppf_roundtrip(self):
        d = LogNormal(0.5, 1.5)
        for p in (1e-4, 0.01, 0.5, 0.99, 0.9999):
            self.assertAlmostEqual(d.cdf(d.ppf(p)), p, places=9)
        self.assertEqual(d.cdf(0.0), 0.0)
        self.assertEqual(d.cdf(-3.0), 0.0)


class TestGoodnessOfFit(unittest.TestCase):
    def test_correct_distributions_accepted(self):
        cases = [
            (Uniform(-2, 5), lambda s: s.uniform(5000, -2, 5)),
            (Exponential(0.7), lambda s: s.exponential(5000, 0.7)),
            (Normal(3, 2.5), lambda s: s.normal(5000, 3, 2.5)),
            (Binomial(12, 0.4), lambda s: s.binomial(5000, 12, 0.4)),
        ]
        for i, (dist, gen) in enumerate(cases):
            samples = gen(Sampler(StdRandomSource(100 + i)))
            result = goodness_of_fit(samples, dist, alpha=0.05)
            self.assertEqual(result.conclusion, "accept",
                             f"{dist.name} wrongly rejected:\n{result}")

    def test_wrong_distribution_rejected(self):
        samples = Sampler(StdRandomSource(9)).exponential(5000, 1.0)
        result = goodness_of_fit(samples, Normal(1.0, 1.0))
        self.assertEqual(result.conclusion, "reject")
        self.assertGreater(result.statistic, result.critical)

    def test_shifted_distribution_rejected(self):
        # 均值相同但分布形状不同（均匀 vs 正态），均值检验看不出，卡方能看出
        samples = Sampler(StdRandomSource(11)).uniform(5000, -math.sqrt(3), math.sqrt(3))
        result = goodness_of_fit(samples, Normal(0, 1))
        self.assertEqual(result.conclusion, "reject")

    def test_small_sample_uses_ks(self):
        samples = Sampler(StdRandomSource(5)).normal(6, 0, 1)
        result = goodness_of_fit(samples, Normal(0, 1))
        self.assertEqual(result.method, "kolmogorov-smirnov")
        self.assertEqual(result.conclusion, "accept")

    def test_small_sample_reject_obvious_mismatch(self):
        samples = Sampler(StdRandomSource(5)).exponential(8, 1.0)
        result = goodness_of_fit(samples, Uniform(0, 0.5))
        self.assertEqual(result.conclusion, "reject")

    def test_quantile_comparison(self):
        samples = Sampler(StdRandomSource(3)).normal(20000, 0, 1)
        rows = quantile_comparison(samples, Normal(0, 1).ppf)
        for q, theo, samp, rel in rows:
            self.assertLess(rel, 0.1, f"quantile {q}: theo={theo} samp={samp}")

    def test_poisson_accept_and_reject(self):
        samples = Sampler(StdRandomSource(120)).poisson(5000, 4.0)
        self.assertEqual(goodness_of_fit(samples, Poisson(4.0)).conclusion,
                         "accept")
        result = goodness_of_fit(samples, Poisson(10.0))
        self.assertEqual(result.conclusion, "reject")
        self.assertGreater(result.statistic, result.critical)

    def test_lognormal_accept_and_reject(self):
        samples = Sampler(StdRandomSource(121)).lognormal(5000, 0.5, 0.8)
        self.assertEqual(goodness_of_fit(samples, LogNormal(0.5, 0.8)).conclusion,
                         "accept")
        # 对数正态样本明显不是对称正态
        self.assertEqual(goodness_of_fit(samples, Normal(1, 1)).conclusion,
                         "reject")


class TestQuantileTest(unittest.TestCase):
    def test_correct_distribution_accepted(self):
        samples = Sampler(StdRandomSource(130)).normal(5000, 1, 2)
        result = quantile_test(samples, Normal(1, 2))
        self.assertEqual(result.method, "quantile-comparison")
        self.assertEqual(result.df, 5)
        self.assertEqual(result.conclusion, "accept")
        self.assertLess(result.statistic, result.critical)

    def test_wrong_distribution_rejected(self):
        samples = Sampler(StdRandomSource(131)).exponential(5000, 1.0)
        result = quantile_test(samples, Normal(1, 1))
        self.assertEqual(result.conclusion, "reject")
        self.assertGreater(result.statistic, result.critical)

    def test_lognormal_samples(self):
        samples = Sampler(StdRandomSource(132)).lognormal(5000, 0.0, 1.0)
        self.assertEqual(quantile_test(samples, LogNormal(0, 1)).conclusion,
                         "accept")
        self.assertEqual(quantile_test(samples, Normal(1, 1)).conclusion,
                         "reject")

    def test_df_reduced_by_estimated_params(self):
        samples = Sampler(StdRandomSource(133)).normal(5000, 0, 1)
        result = quantile_test(samples, Normal(0, 1), n_estimated=2)
        self.assertEqual(result.df, 3)

    def test_discrete_distribution_rejected_by_api(self):
        samples = Sampler(StdRandomSource(134)).poisson(500, 3.0)
        with self.assertRaises(ValueError):
            quantile_test(samples, Poisson(3.0))

    def test_invalid_quantiles(self):
        samples = Sampler(StdRandomSource(135)).normal(500, 0, 1)
        with self.assertRaises(ValueError):
            quantile_test(samples, Normal(0, 1), quantiles=[])
        with self.assertRaises(ValueError):
            quantile_test(samples, Normal(0, 1), quantiles=[0.5, 1.0])


class TestFit(unittest.TestCase):
    def test_fit_recovers_params(self):
        cases = [
            ("uniform", lambda s: s.uniform(20000, -1.5, 4.0),
             {"a": -1.5, "b": 4.0}, 0.02),
            ("exponential", lambda s: s.exponential(20000, 2.0),
             {"lam": 2.0}, 0.06),
            ("normal", lambda s: s.normal(20000, 1.0, 2.0),
             {"mu": 1.0, "sigma": 2.0}, 0.02),
            ("binomial", lambda s: s.binomial(20000, 15, 0.3),
             {"n": 15, "p": 0.3}, None),
        ]
        for i, (family, gen, truth, tol) in enumerate(cases):
            samples = gen(Sampler(StdRandomSource(200 + i)))
            result = fit(samples, family)
            self.assertEqual(result.test.conclusion, "accept",
                             f"{family} fit rejected:\n{result}")
            params = result.params()
            for key, want in truth.items():
                got = params[key]
                if tol is None:
                    self.assertAlmostEqual(got, want, delta=0.02 if key == "p" else 0.5)
                else:
                    self.assertAlmostEqual(got, want, delta=tol)

    def test_fit_rejects_mismatched_sample(self):
        # 指数样本强行拟合正态：必须拒绝
        samples = Sampler(StdRandomSource(77)).exponential(5000, 1.0)
        result = fit(samples, "normal")
        self.assertEqual(result.test.conclusion, "reject")
        self.assertGreater(result.test.statistic, result.test.critical)

    def test_fit_rejects_uniform_as_exponential(self):
        samples = Sampler(StdRandomSource(78)).uniform(5000, 0, 2)
        result = fit(samples, "exponential")
        self.assertEqual(result.test.conclusion, "reject")

    def test_fit_best_picks_right_family(self):
        samples = Sampler(StdRandomSource(88)).normal(5000, 5, 1.5)
        best = fit_best(samples)[0]
        self.assertEqual(best.family, "normal")
        self.assertEqual(best.test.conclusion, "accept")

    def test_fit_degenerate_sample(self):
        with self.assertRaises(ValueError):
            fit([3.0] * 100, "normal")
        with self.assertRaises(ValueError):
            fit([2.0] * 100, "uniform")

    def test_fit_poisson_recovers_lam(self):
        samples = Sampler(StdRandomSource(210)).poisson(20000, 3.2)
        result = fit(samples, "poisson")
        self.assertEqual(result.test.conclusion, "accept")
        self.assertAlmostEqual(result.params()["lam"], 3.2, delta=0.06)

    def test_fit_lognormal_recovers_params(self):
        samples = Sampler(StdRandomSource(211)).lognormal(20000, 0.5, 0.8)
        result = fit(samples, "lognormal")
        self.assertEqual(result.test.conclusion, "accept")
        self.assertAlmostEqual(result.params()["mu"], 0.5, delta=0.02)
        self.assertAlmostEqual(result.params()["sigma"], 0.8, delta=0.02)

    def test_fit_poisson_rejects_non_integer(self):
        with self.assertRaises(ValueError):
            fit([1.5, 2.5, 3.5], "poisson")
        with self.assertRaises(ValueError):
            fit([-1, 0, 1], "poisson")

    def test_fit_lognormal_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            fit([1.0, 0.0, 2.0], "lognormal")
        with self.assertRaises(ValueError):
            fit([1.0, -2.0, 3.0], "lognormal")

    def test_fit_best_picks_lognormal(self):
        samples = Sampler(StdRandomSource(212)).lognormal(5000, 0.3, 0.9)
        best = fit_best(samples)[0]
        self.assertEqual(best.family, "lognormal")
        self.assertEqual(best.test.conclusion, "accept")


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in __import__("sys").argv else 1)
