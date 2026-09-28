"""从样本反推分布参数（矩估计/MLE），并立即做拟合优度检验。

对明显不匹配的样本（如用正态去拟合指数样本）会给出 reject 结论，
拒绝依据来自卡方/KS 统计量超过临界值。
"""
import math

from . import distributions as dist_mod
from . import gof

FAMILIES = ("uniform", "exponential", "normal", "lognormal", "binomial", "poisson")


class FitResult:
    def __init__(self, family, dist, test):
        self.family = family
        self.dist = dist
        self.test = test

    def params(self):
        return self.dist.params()

    def __str__(self):
        params = ", ".join(f"{k}={v:.6g}" for k, v in self.params().items())
        return f"family={self.family}  params: {params}\n{self.test}"


def _check(samples):
    xs = list(samples)
    if len(xs) == 0:
        raise ValueError("empty sample: cannot fit parameters")
    if len(xs) < 2:
        raise ValueError("need at least 2 observations to fit parameters")
    for x in xs:
        if not math.isfinite(x):
            raise ValueError("sample contains non-finite value")
    return xs


def fit(samples, family, alpha=0.05):
    """拟合指定分布族并检验。返回 FitResult；test.conclusion 给出 accept/reject。"""
    xs = _check(samples)
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; choose from {FAMILIES}")
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n

    if family == "uniform":
        # MLE: a=min, b=max
        dist = dist_mod.Uniform(min(xs), max(xs)) if min(xs) < max(xs) else None
        if dist is None:
            raise ValueError("degenerate sample (all values equal): cannot fit uniform")
    elif family == "exponential":
        if mean <= 0:
            raise ValueError("sample mean <= 0: exponential fit impossible "
                             "(data likely not exponential)")
        dist = dist_mod.Exponential(1.0 / mean)  # MLE: lam = 1/mean
    elif family == "normal":
        sigma = math.sqrt(var)
        if sigma == 0.0:
            raise ValueError("degenerate sample (zero variance): cannot fit normal")
        dist = dist_mod.Normal(mean, sigma)      # MLE
    elif family == "lognormal":
        if any(x <= 0 for x in xs):
            raise ValueError("lognormal fit requires strictly positive observations")
        logs = [math.log(x) for x in xs]
        lmean = sum(logs) / n
        lvar = sum((v - lmean) ** 2 for v in logs) / n
        if lvar == 0.0:
            raise ValueError("degenerate sample (zero log-variance): "
                             "cannot fit lognormal")
        dist = dist_mod.LogNormal(lmean, math.sqrt(lvar))  # MLE
    elif family == "poisson":
        rounded = [round(x) for x in xs]
        if any(abs(x - r) > 1e-9 or r < 0 for x, r in zip(xs, rounded)):
            raise ValueError("poisson fit requires non-negative integer observations")
        # MLE: lam = 样本均值；mean=0 时为 Poisson(0) 退化分布（零方差）
        dist = dist_mod.Poisson(mean)
    else:  # binomial
        rounded = [round(x) for x in xs]
        if any(abs(x - r) > 1e-9 or r < 0 for x, r in zip(xs, rounded)):
            raise ValueError("binomial fit requires non-negative integer observations")
        if var < mean and mean > 0:
            # 矩估计同时反推 n 与 p
            n_est = max(1, round(mean * mean / (mean - var)))
            p_est = min(1.0, max(0.0, mean / n_est))
        else:
            # var >= mean 不像二项；退化为用最大值作 n，检验大概率会拒绝
            n_est = max(1, int(max(rounded)))
            p_est = min(1.0, mean / n_est) if n_est else 0.0
        dist = dist_mod.Binomial(n_est, p_est)

    test = gof.goodness_of_fit(xs, dist, n_estimated=dist.n_params, alpha=alpha)
    return FitResult(family, dist, test)


def fit_best(samples, alpha=0.05):
    """尝试全部分布族，按 p 值排序返回；第一个即最佳拟合。"""
    xs = _check(samples)
    results = []
    for family in FAMILIES:
        try:
            results.append(fit(xs, family, alpha=alpha))
        except ValueError:
            continue
    results.sort(key=lambda r: (r.test.p_value != r.test.p_value,  # NaN 排最后
                                -(r.test.p_value if r.test.p_value == r.test.p_value else 0)))
    return results
