"""Special math functions implemented with the standard library only.

Provides the pieces needed for distribution-level goodness-of-fit tests:
regularized incomplete gamma (chi-square CDF and its inverse), the inverse
normal CDF, and the Kolmogorov limiting distribution for KS tests.
"""

import math

_EPS = 1e-14
_FPMIN = 1e-300
_MAX_ITER = 1000


def gammainc_lower_reg(a, x):
    """Regularized lower incomplete gamma P(a, x), via series / continued
    fraction (Numerical Recipes, gammp). Accurate to ~1e-12."""
    if x < 0 or a <= 0:
        raise ValueError("require x >= 0 and a > 0")
    if x == 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        # Series representation.
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(_MAX_ITER):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                return total * math.exp(-x + a * math.log(x) - gln)
        raise ArithmeticError("gammainc series did not converge")
    # Continued fraction for Q(a, x); P = 1 - Q.
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return 1.0 - q


def chi2_cdf(x, df):
    """CDF of the chi-square distribution with `df` degrees of freedom."""
    if df <= 0:
        raise ValueError("df must be positive")
    if x <= 0:
        return 0.0
    return gammainc_lower_reg(df / 2.0, x / 2.0)


def chi2_ppf(p, df):
    """Inverse CDF (critical value) of chi-square via bisection with a
    Wilson-Hilferty starting bracket."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if df <= 0:
        raise ValueError("df must be positive")
    # Wilson-Hilferty approximation gives a good initial guess.
    z = norm_ppf(p)
    t = 1.0 - 2.0 / (9.0 * df) + z * math.sqrt(2.0 / (9.0 * df))
    guess = df * t ** 3
    lo, hi = 0.0, max(guess * 2.0, df + 10.0 * math.sqrt(2.0 * df), 1.0)
    while chi2_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def norm_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation,
    max abs error ~1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def kolmogorov_sf(lam):
    """Survival function of the Kolmogorov limiting distribution:
    Q(lam) = 2 * sum_{k>=1} (-1)^{k-1} exp(-2 k^2 lam^2).
    Used for asymptotic KS-test p-values (valid for n not tiny)."""
    if lam <= 0:
        return 1.0
    total = 0.0
    for k in range(1, 1000):
        term = 2.0 * ((-1.0) ** (k - 1)) * math.exp(-2.0 * k * k * lam * lam)
        total += term
        if abs(term) < 1e-15:
            break
    return min(1.0, max(0.0, total))
