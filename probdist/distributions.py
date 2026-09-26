"""Samplers for common distributions, driven by an injectable RNG.

Every sampler takes an `rng` argument: any object exposing `random()`
returning floats in [0, 1) (e.g. `random.Random(seed)`). Same seed =>
identical sample sequences, which makes runs reproducible.

Each distribution also exposes its CDF / quantile / PMF so the
goodness-of-fit module can build exact expected counts.
"""

import math


# ---------------------------------------------------------------- uniform

def check_uniform_params(low, high):
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("uniform bounds must be finite")
    if not low < high:
        raise ValueError("require low < high")


def sample_uniform(rng, low=0.0, high=1.0, n=1):
    check_uniform_params(low, high)
    _check_n(n)
    return [low + (high - low) * rng.random() for _ in range(n)]


def uniform_cdf(x, low, high):
    if x <= low:
        return 0.0
    if x >= high:
        return 1.0
    return (x - low) / (high - low)


def uniform_ppf(p, low, high):
    return low + (high - low) * p


# ------------------------------------------------------------- exponential

def check_exponential_params(lam):
    if not (math.isfinite(lam) and lam > 0):
        raise ValueError("require lam > 0")


def sample_exponential(rng, lam=1.0, n=1):
    check_exponential_params(lam)
    _check_n(n)
    out = []
    for _ in range(n):
        u = rng.random()
        # 1 - U is also uniform; guard the u == 0.0 edge so log is finite.
        out.append(-math.log(1.0 - u) / lam)
    return out


def exponential_cdf(x, lam):
    if x <= 0:
        return 0.0
    return 1.0 - math.exp(-lam * x)


def exponential_ppf(p, lam):
    return -math.log(1.0 - p) / lam


# ----------------------------------------------------------------- normal

def check_normal_params(mu, sigma):
    if not math.isfinite(mu):
        raise ValueError("mu must be finite")
    if not (math.isfinite(sigma) and sigma > 0):
        raise ValueError("require sigma > 0")


def sample_normal(rng, mu=0.0, sigma=1.0, n=1):
    """Box-Muller transform; caches the spare deviate per call batch."""
    check_normal_params(mu, sigma)
    _check_n(n)
    out = []
    while len(out) < n:
        u1 = rng.random()
        # Avoid log(0): redraw in the (measure-zero but possible) u1 == 0 case.
        while u1 <= 0.0:
            u1 = rng.random()
        u2 = rng.random()
        r = math.sqrt(-2.0 * math.log(u1))
        out.append(mu + sigma * r * math.cos(2.0 * math.pi * u2))
        if len(out) < n:
            out.append(mu + sigma * r * math.sin(2.0 * math.pi * u2))
    return out


def normal_cdf(x, mu, sigma):
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


# ---------------------------------------------------------------- binomial

def check_binomial_params(trials, p):
    if not isinstance(trials, int) or trials < 0:
        raise ValueError("require integer trials >= 0")
    if not (0.0 <= p <= 1.0):
        raise ValueError("require 0 <= p <= 1")


def sample_binomial(rng, trials, p, n=1):
    check_binomial_params(trials, p)
    _check_n(n)
    return [sum(1 for _ in range(trials) if rng.random() < p) for _ in range(n)]


def binomial_pmf(k, trials, p):
    if k < 0 or k > trials:
        return 0.0
    if p == 0.0:
        return 1.0 if k == 0 else 0.0
    if p == 1.0:
        return 1.0 if k == trials else 0.0
    log_pmf = (math.lgamma(trials + 1) - math.lgamma(k + 1)
               - math.lgamma(trials - k + 1)
               + k * math.log(p) + (trials - k) * math.log(1.0 - p))
    return math.exp(log_pmf)


def _check_n(n):
    if not isinstance(n, int) or n < 0:
        raise ValueError("sample size n must be a non-negative integer")
