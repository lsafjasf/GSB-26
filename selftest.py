"""自测：python3 selftest.py [-v]"""
import math
import unittest

from distfit import (Sampler, StdRandomSource, Uniform, Exponential, Normal,
                     Binomial, goodness_of_fit, ks_test, quantile_comparison,
                     fit, fit_best)
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
                     lambda: Sampler(StdRandomSource(42)).binomial(200, 10, 0.3)):
            self.assertEqual(make(), make())

    def test_injected_source_is_used(self):
        class ConstantSource:
            def random(self):
                return 0.5
        s = Sampler(ConstantSource())
        self.assertEqual(s.uniform(3, 2.0, 4.0), [3.0, 3.0, 3.0])
        self.assertAlmostEqual(s.exponential(1, 2.0)[0], math.log(2) / 2.0)

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


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in __import__("sys").argv else 1)
