"""Goodness-of-fit tests: chi-square, Kolmogorov-Smirnov, quantile check.

Each test returns a TestResult carrying the statistic, the critical value at
the requested significance level, the p-value, and a human-readable
conclusion -- never just "mean looks fine".

Approximation limits (also emitted as warnings in the result):
- Chi-square: expected count per bin should be >= ~5. With tiny samples
  (single digits) the chi-square approximation of the statistic's null
  distribution breaks down; treat results as indicative only.
- KS p-value uses the Kolmogorov limiting distribution; it is conservative
  for small n and for discrete data.
"""

import math
from dataclasses import dataclass, field

from .special import chi2_cdf, chi2_ppf, kolmogorov_sf


@dataclass
class TestResult:
    test: str
    statistic: float
    critical_value: float
    p_value: float
    alpha: float
    passed: bool
    conclusion: str
    warnings: list = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def __str__(self):
        lines = [
            f"[{self.test}] statistic={self.statistic:.6g} "
            f"critical({self.alpha})={self.critical_value:.6g} "
            f"p-value={self.p_value:.4g} -> {self.conclusion}",
        ]
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def _finalize(test, stat, crit, p, alpha, warnings, details):
    passed = stat <= crit
    conclusion = ("PASS: no evidence against the hypothesized distribution"
                  if passed else
                  "FAIL: sample is inconsistent with the hypothesized distribution")
    return TestResult(test, stat, crit, p, alpha, passed, conclusion,
                      warnings, details)


# ------------------------------------------------------------- chi-square

def chi_square_uniform_bins(samples, cdf, ppf, n_bins=None, alpha=0.05,
                            n_fitted_params=0):
    """Chi-square test for a continuous distribution.

    Bins are equiprobable under the hypothesized distribution (built from
    its quantile function), so the expected count per bin is n / n_bins.
    `n_fitted_params` reduces degrees of freedom when parameters were
    estimated from the same data.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("cannot test an empty sample")
    if n_bins is None:
        # Common rule of thumb, kept within sane bounds.
        n_bins = max(4, min(100, int(round(2.0 * n ** 0.4))))
    # Endpoints are infinite for unbounded distributions; only interior
    # edges come from the quantile function.
    edges = [float('-inf')] + [ppf(i / n_bins) for i in range(1, n_bins)] \
        + [float('inf')]
    expected = n / n_bins
    counts = [0] * n_bins
    for x in samples:
        if x <= edges[0]:
            counts[0] += 1
        elif x >= edges[-1]:
            counts[-1] += 1
        else:
            lo, hi = 0, n_bins
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if x < edges[mid]:
                    hi = mid
                else:
                    lo = mid
            counts[lo] += 1
    stat = sum((c - expected) ** 2 / expected for c in counts)
    df = n_bins - 1 - n_fitted_params
    warnings = []
    if expected < 5.0:
        warnings.append(
            f"expected count per bin is {expected:.2f} (< 5); chi-square "
            "approximation unreliable, treat result as indicative only")
    if n < 10:
        warnings.append(
            f"sample size {n} is very small; no distribution test has "
            "meaningful power here")
    if df < 1:
        raise ValueError("not enough bins for the number of fitted parameters")
    crit = chi2_ppf(1.0 - alpha, df)
    p = 1.0 - chi2_cdf(stat, df)
    return _finalize("chi-square", stat, crit, p, alpha, warnings,
                     {"bins": counts, "expected_per_bin": expected, "df": df})


def chi_square_discrete(samples, support_pmf, alpha=0.05, n_fitted_params=0):
    """Chi-square test for a discrete distribution given as {value: pmf}.

    Adjacent outcomes are merged until each group has expected count >= 5
    (Cochran's rule); if that is impossible the test refuses to run.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("cannot test an empty sample")
    items = sorted(support_pmf.items())
    groups, cur_vals, cur_exp = [], [], 0.0
    for value, prob in items:
        cur_vals.append(value)
        cur_exp += prob * n
        if cur_exp >= 5.0:
            groups.append((cur_vals, cur_exp))
            cur_vals, cur_exp = [], 0.0
    if cur_vals:
        if groups:
            vals, exp = groups[-1]
            groups[-1] = (vals + cur_vals, exp + cur_exp)
        else:
            groups.append((cur_vals, cur_exp))
    observed = {}
    for x in samples:
        observed[x] = observed.get(x, 0) + 1
    stat = 0.0
    for vals, exp in groups:
        obs = sum(observed.get(v, 0) for v in vals)
        stat += (obs - exp) ** 2 / exp
    df = len(groups) - 1 - n_fitted_params
    warnings = []
    if n < 10:
        warnings.append(
            f"sample size {n} is very small; no distribution test has "
            "meaningful power here")
    if groups and min(e for _, e in groups) < 5.0:
        warnings.append("some merged groups still have expected count < 5")
    if df < 1:
        raise ValueError(
            "too few outcome groups after merging; sample too small for a "
            "chi-square test on this distribution")
    crit = chi2_ppf(1.0 - alpha, df)
    p = 1.0 - chi2_cdf(stat, df)
    return _finalize("chi-square(discrete)", stat, crit, p, alpha, warnings,
                     {"groups": len(groups), "df": df})


# ------------------------------------------------------------------- KS

def ks_test(samples, cdf, alpha=0.05):
    """One-sample Kolmogorov-Smirnov test against a continuous CDF.

    Critical value uses the asymptotic Kolmogorov distribution; for
    n < ~35 it is conservative (rejects less often than it should).
    """
    n = len(samples)
    if n == 0:
        raise ValueError("cannot test an empty sample")
    xs = sorted(samples)
    d_plus = max((i + 1) / n - cdf(x) for i, x in enumerate(xs))
    d_minus = max(cdf(x) - i / n for i, x in enumerate(xs))
    stat = max(d_plus, d_minus)
    # Invert the asymptotic p-value formula numerically for the critical D.
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        lam = mid * math.sqrt(n)
        if kolmogorov_sf(lam) > alpha:
            lo = mid
        else:
            hi = mid
    crit = 0.5 * (lo + hi)
    p = kolmogorov_sf(stat * math.sqrt(n))
    warnings = []
    if n < 35:
        warnings.append(
            f"n={n}: asymptotic KS critical values are conservative for "
            "small samples")
    return _finalize("kolmogorov-smirnov", stat, crit, p, alpha, warnings,
                     {"d_plus": d_plus, "d_minus": d_minus})


# ------------------------------------------------- quantile spot comparison

def quantile_check(samples, ppf, probs=(0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99),
                   alpha=0.05):
    """Compare empirical quantiles with theoretical ones.

    Each empirical quantile is a binomial count; the check flags quantiles
    whose observed count falls outside a two-sided binomial acceptance band
    (Bonferroni-corrected across quantiles). Useful for tail behaviour,
    where mean/variance checks are blind.

    The statistic is the number of out-of-band quantiles. Under H0 each
    quantile falls outside its band with probability ~alpha/len(probs), so
    the statistic follows ~Binomial(len(probs), alpha/len(probs)); the
    critical value and the p-value are taken from that null distribution.
    The decision rule matches the other tests: reject iff
    statistic > critical value (equivalently p-value <= alpha).
    """
    n = len(samples)
    if n == 0:
        raise ValueError("cannot test an empty sample")
    if not probs:
        raise ValueError("at least one quantile probability is required")
    xs = sorted(samples)
    per_test_alpha = alpha / len(probs)
    mismatches = []
    rows = []
    for q in probs:
        theo = ppf(q)
        # Count of samples below the theoretical q-quantile ~ Binomial(n, q).
        count = sum(1 for x in xs if x < theo)
        lo = _binom_quantile(per_test_alpha / 2, n, q)
        hi = _binom_quantile(1.0 - per_test_alpha / 2, n, q)
        ok = lo <= count <= hi
        rows.append({"q": q, "theoretical": theo, "count_below": count,
                     "accept_band": (lo, hi), "ok": ok})
        if not ok:
            mismatches.append(q)
    stat = float(len(mismatches))
    m = len(probs)
    crit = float(_binom_quantile(1.0 - alpha, m, per_test_alpha))
    p = 1.0 - _binom_cdf(int(stat) - 1, m, per_test_alpha)
    warnings = []
    if n < 50:
        warnings.append(
            f"n={n}: quantile bands are wide for small n; tails "
            "(1%/99%) are essentially untestable")
    passed = stat <= crit
    conclusion = ("PASS: empirical quantiles within binomial acceptance bands"
                  if passed else
                  f"FAIL: quantiles {mismatches} fall outside acceptance bands")
    return TestResult("quantile-check", stat, crit, p, alpha, passed,
                      conclusion, warnings, {"rows": rows})


def _binom_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), via PMF recurrence from the mode."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    m = min(n, int((n + 1) * p))
    logpmf = (math.lgamma(n + 1) - math.lgamma(m + 1) - math.lgamma(n - m + 1)
              + m * math.log(p) + (n - m) * math.log(1.0 - p))
    pmf_m = math.exp(logpmf)
    # CDF at the mode: sum downward (terms shrink, no underflow issue).
    cdf = pmf_m
    pmf = pmf_m
    for j in range(m, 0, -1):
        pmf *= j * (1.0 - p) / ((n - j + 1) * p)
        cdf += pmf
    if k >= m:
        pmf = pmf_m
        for j in range(m, k):
            pmf *= (n - j) * p / ((j + 1) * (1.0 - p))
            cdf += pmf
        return min(1.0, cdf)
    pmf = pmf_m
    for j in range(m, k, -1):
        cdf -= pmf
        pmf *= j * (1.0 - p) / ((n - j + 1) * p)
    return max(0.0, cdf)


def _binom_quantile(prob, n, p):
    """Smallest k with P(X <= k) >= prob for X ~ Binomial(n, p).

    Works from the mode outward with PMF recurrence, so it stays accurate
    for large n where (1-p)**n would underflow to zero.
    """
    if prob <= 0:
        return 0
    if prob >= 1:
        return n
    if p <= 0:
        return 0
    if p >= 1:
        return n
    m = min(n, int((n + 1) * p))
    logpmf = (math.lgamma(n + 1) - math.lgamma(m + 1) - math.lgamma(n - m + 1)
              + m * math.log(p) + (n - m) * math.log(1.0 - p))
    pmf_m = math.exp(logpmf)
    # cdf at the mode: sum downward (terms shrink, no underflow issue).
    cdf = pmf_m
    pmf = pmf_m
    for k in range(m, 0, -1):
        pmf *= k * (1.0 - p) / ((n - k + 1) * p)
        cdf += pmf
    if cdf < prob:
        k, pmf = m, pmf_m
        while cdf < prob and k < n:
            pmf *= (n - k) * p / ((k + 1) * (1.0 - p))
            k += 1
            cdf += pmf
        return k
    k, pmf = m, pmf_m
    while k > 0 and cdf - pmf >= prob:
        cdf -= pmf
        pmf *= (k) * (1.0 - p) / ((n - k + 1) * p)
        k -= 1
    return k
