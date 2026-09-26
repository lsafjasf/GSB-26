"""Fit distribution parameters from samples and test the fit.

Estimators:
- uniform(low, high): MLE = (min, max) of the sample.
- exponential(lam):   MLE = 1 / sample mean.
- normal(mu, sigma):  MLE = (mean, sqrt(variance with 1/n)).
- binomial(trials, p): p = mean / trials (trials given or moment-estimated).

Each fit runs a chi-square goodness-of-fit test with degrees of freedom
reduced by the number of estimated parameters. A clearly mismatched sample
(e.g. exponential data fitted as normal) is rejected with the statistic,
critical value and p-value as the stated evidence.
"""

import math
from dataclasses import dataclass

from . import distributions as dist
from . import gof
from .special import norm_ppf


@dataclass
class FitResult:
    distribution: str
    params: dict
    gof: gof.TestResult

    def __str__(self):
        params = ", ".join(f"{k}={v:.6g}" for k, v in self.params.items())
        return f"fit {self.distribution}({params})\n{self.gof}"


def _require_nonempty(samples):
    if len(samples) == 0:
        raise ValueError("cannot fit an empty sample")


def fit_uniform(samples, alpha=0.05):
    _require_nonempty(samples)
    low, high = min(samples), max(samples)
    if low == high:
        raise ValueError("degenerate sample: all values identical")
    result = gof.chi_square_uniform_bins(
        samples,
        cdf=lambda x: dist.uniform_cdf(x, low, high),
        ppf=lambda p: dist.uniform_ppf(p, low, high),
        alpha=alpha, n_fitted_params=2)
    return FitResult("uniform", {"low": low, "high": high}, result)


def fit_exponential(samples, alpha=0.05):
    _require_nonempty(samples)
    if min(samples) < 0:
        raise ValueError("exponential samples must be non-negative")
    mean = sum(samples) / len(samples)
    if mean == 0:
        raise ValueError("degenerate sample: all zeros")
    lam = 1.0 / mean
    result = gof.chi_square_uniform_bins(
        samples,
        cdf=lambda x: dist.exponential_cdf(x, lam),
        ppf=lambda p: dist.exponential_ppf(p, lam),
        alpha=alpha, n_fitted_params=1)
    return FitResult("exponential", {"lam": lam}, result)


def fit_normal(samples, alpha=0.05):
    _require_nonempty(samples)
    n = len(samples)
    mu = sum(samples) / n
    var = sum((x - mu) ** 2 for x in samples) / n
    if var == 0:
        raise ValueError("degenerate sample: zero variance")
    sigma = math.sqrt(var)
    result = gof.chi_square_uniform_bins(
        samples,
        cdf=lambda x: dist.normal_cdf(x, mu, sigma),
        ppf=lambda p: mu + sigma * norm_ppf(p),
        alpha=alpha, n_fitted_params=2)
    return FitResult("normal", {"mu": mu, "sigma": sigma}, result)


def fit_binomial(samples, trials=None, alpha=0.05):
    _require_nonempty(samples)
    if any((not float(x).is_integer()) or x < 0 for x in samples):
        raise ValueError("binomial samples must be non-negative integers")
    n = len(samples)
    mean = sum(samples) / n
    if trials is None:
        # Method of moments for both parameters: trials = mean^2/(mean-var).
        var = sum((x - mean) ** 2 for x in samples) / n
        if var >= mean:
            raise ValueError(
                "sample variance >= mean: data inconsistent with any "
                "binomial distribution (cannot estimate trials)")
        trials = max(1, round(mean * mean / (mean - var)))
    if max(samples) > trials:
        raise ValueError(
            f"sample contains values above trials={trials}: cannot be "
            "binomial with these parameters")
    p = mean / trials if trials > 0 else 0.0
    pmf = {k: dist.binomial_pmf(k, trials, p) for k in range(trials + 1)}
    result = gof.chi_square_discrete(samples, pmf, alpha=alpha,
                                     n_fitted_params=1)
    return FitResult("binomial", {"trials": trials, "p": p}, result)
