"""纯标准库数值函数：正则化不完全伽马函数、卡方/正态 CDF 与分位数。

实现参考 Numerical Recipes 的级数/连分式算法，精度约 1e-14，
足以支撑统计检验的临界值与 p 值计算。
"""
import math

_EPS = 1e-14
_TINY = 1e-300


def gamma_p(a, x):
    """正则化下不完全伽马函数 P(a, x)。"""
    if a <= 0:
        raise ValueError("a must be positive")
    if x <= 0:
        return 0.0
    if x < a + 1.0:
        # 级数展开
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(10000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return 1.0 - gamma_q(a, x)


def gamma_q(a, x):
    """正则化上不完全伽马函数 Q(a, x)，连分式展开。"""
    if a <= 0:
        raise ValueError("a must be positive")
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - gamma_p(a, x)
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def chi2_cdf(x, df):
    """自由度 df 的卡方分布 CDF。"""
    if df <= 0:
        raise ValueError("df must be positive")
    return gamma_p(df / 2.0, x / 2.0)


def chi2_ppf(p, df):
    """卡方分布分位数（二分 + 初值加速）。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if df <= 0:
        raise ValueError("df must be positive")
    # Wilson-Hilferty 近似作为初值区间中点
    z = _norm_ppf(p)
    t = 1.0 - 2.0 / (9.0 * df) + z * math.sqrt(2.0 / (9.0 * df))
    guess = df * t ** 3
    lo, hi = 0.0, max(guess * 2.0, df, 1.0)
    while chi2_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def normal_cdf(x, mu=0.0, sigma=1.0):
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _norm_ppf(p):
    """标准正态分位数，Acklam 有理逼近 + 一步 Halley 修正，精度约 1e-15。"""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
             ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    # Halley 一步精化
    e = normal_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def normal_ppf(p, mu=0.0, sigma=1.0):
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    return mu + sigma * _norm_ppf(p)


def ks_pvalue(d, n):
    """KS 统计量的渐近 p 值（Stephens 有限样本修正 + Kolmogorov 级数）。"""
    if n <= 0:
        raise ValueError("n must be positive")
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    total = 0.0
    for k in range(1, 101):
        term = (-1) ** (k - 1) * math.exp(-2.0 * k * k * lam * lam)
        total += term
        if abs(term) < 1e-12:
            break
    return min(1.0, max(0.0, 2.0 * total))


def ks_critical(n, alpha):
    """KS 检验在显著性 alpha 下的临界 D 值（对 ks_pvalue 二分求解）。"""
    lo, hi = 0.0, 3.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if ks_pvalue(mid, n) > alpha:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
