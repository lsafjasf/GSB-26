"""拟合优度检验：卡方检验（连续等概率分箱 / 离散自动合并）、KS 检验、
分位数对比检验（样本分位数联合渐近卡方）。

小样本策略：
- n < 2：拒绝执行（ValueError），统计上没有意义。
- 连续分布且 n < 30：卡方近似不可靠，改用 KS 检验（小样本有效）。
- 离散分布：自动把期望频数 < 5 的相邻类别合并；合并后自由度不足则报 inconclusive。
注意：参数由样本估计时，KS 的 p 值偏保守（Lilliefors 情形），报告中会标注。
"""
import math

from . import special

MIN_EXPECTED = 5.0      # 卡方近似的常规下限（Cochran 规则）
SMALL_SAMPLE = 30       # 连续分布小样本改用 KS 的阈值


class TestResult:
    def __init__(self, method, statistic, critical, p_value, df=None,
                 alpha=0.05, conclusion="inconclusive", notes=None):
        self.method = method
        self.statistic = statistic
        self.critical = critical
        self.p_value = p_value
        self.df = df
        self.alpha = alpha
        self.conclusion = conclusion  # "accept" / "reject" / "inconclusive"
        self.notes = notes or []

    @property
    def rejected(self):
        return self.conclusion == "reject"

    def __str__(self):
        lines = [
            f"method     : {self.method}",
            f"statistic  : {self.statistic:.6g}",
            f"critical   : {self.critical:.6g} (alpha={self.alpha})",
            f"p-value    : {self.p_value:.6g}",
        ]
        if self.df is not None:
            lines.append(f"df         : {self.df}")
        lines.append(f"conclusion : {self.conclusion}")
        for note in self.notes:
            lines.append(f"note       : {note}")
        return "\n".join(lines)


def _check_samples(samples):
    xs = list(samples)
    if len(xs) == 0:
        raise ValueError("empty sample: cannot run a goodness-of-fit test on 0 observations")
    if len(xs) < 2:
        raise ValueError("need at least 2 observations for a goodness-of-fit test")
    for x in xs:
        if not math.isfinite(x):
            raise ValueError("sample contains non-finite value")
    return xs


def _chi2_conclusion(stat, df, alpha, notes):
    crit = special.chi2_ppf(1.0 - alpha, df)
    p = special.gamma_q(df / 2.0, stat / 2.0)
    conclusion = "reject" if stat > crit else "accept"
    return TestResult("chi-square", stat, crit, p, df=df, alpha=alpha,
                      conclusion=conclusion, notes=notes)


def chi_square_continuous(samples, cdf, ppf, n_estimated=0, alpha=0.05,
                          n_bins=None):
    """连续分布卡方检验：等概率分箱，每箱期望频数 >= MIN_EXPECTED。"""
    xs = _check_samples(samples)
    n = len(xs)
    if n_bins is None:
        n_bins = max(2, min(50, n // int(MIN_EXPECTED)))
    expected = n / n_bins
    notes = []
    if expected < MIN_EXPECTED:
        notes.append(f"expected count per bin {expected:.2f} < {MIN_EXPECTED}: "
                     "chi-square approximation is unreliable")
    counts = [0] * n_bins
    for x in xs:
        u = min(max(cdf(x), 0.0), 1.0 - 1e-15)
        counts[min(int(u * n_bins), n_bins - 1)] += 1
    stat = sum((c - expected) ** 2 / expected for c in counts)
    df = n_bins - 1 - n_estimated
    if df < 1:
        return TestResult("chi-square", stat, float("nan"), float("nan"),
                          df=df, alpha=alpha, conclusion="inconclusive",
                          notes=["degrees of freedom < 1 after accounting for "
                                 "estimated parameters"])
    if n_estimated:
        notes.append(f"{n_estimated} parameter(s) estimated from data; "
                     "df reduced accordingly (p-value is approximate)")
    return _chi2_conclusion(stat, df, alpha, notes)


def chi_square_discrete(samples, pmf, support, n_estimated=0, alpha=0.05):
    """离散分布卡方检验：相邻类别自动合并直到期望频数 >= MIN_EXPECTED。"""
    xs = _check_samples(samples)
    n = len(xs)
    obs = {}
    for x in xs:
        k = int(round(x))
        obs[k] = obs.get(k, 0) + 1
    keys = sorted(set(support) | set(obs))
    cells = [[obs.get(k, 0), n * pmf(k)] for k in keys]
    # 左到右贪心合并低期望类别
    merged = []
    for cell in cells:
        if merged and merged[-1][1] < MIN_EXPECTED:
            merged[-1][0] += cell[0]
            merged[-1][1] += cell[1]
        else:
            merged.append(list(cell))
    while len(merged) > 1 and merged[-1][1] < MIN_EXPECTED:
        last = merged.pop()
        merged[-1][0] += last[0]
        merged[-1][1] += last[1]
    notes = []
    low = sum(1 for _, e in merged if e < MIN_EXPECTED)
    if low:
        notes.append(f"{low} merged bin(s) still have expected < {MIN_EXPECTED}: "
                     "chi-square approximation is unreliable (small sample)")
    stat = sum((o - e) ** 2 / e for o, e in merged if e > 0)
    df = len(merged) - 1 - n_estimated
    if df < 1:
        return TestResult("chi-square", stat, float("nan"), float("nan"),
                          df=df, alpha=alpha, conclusion="inconclusive",
                          notes=["too few categories after merging; "
                                 "sample too small for a discrete chi-square test"])
    if n_estimated:
        notes.append(f"{n_estimated} parameter(s) estimated from data; "
                     "df reduced accordingly (p-value is approximate)")
    return _chi2_conclusion(stat, df, alpha, notes)


def ks_test(samples, cdf, alpha=0.05, params_estimated=False):
    """Kolmogorov-Smirnov 检验，小样本（n>=2）有效。"""
    xs = sorted(_check_samples(samples))
    n = len(xs)
    d = 0.0
    for i, x in enumerate(xs):
        c = min(max(cdf(x), 0.0), 1.0)
        d = max(d, (i + 1) / n - c, c - i / n)
    p = special.ks_pvalue(d, n)
    crit = special.ks_critical(n, alpha)
    notes = []
    if params_estimated:
        notes.append("parameters estimated from data: KS p-value is conservative "
                     "(Lilliefors case); treat borderline rejects with care")
    return TestResult("kolmogorov-smirnov", d, crit, p, alpha=alpha,
                      conclusion="reject" if d > crit else "accept", notes=notes)


def quantile_comparison(samples, ppf, quantiles=None):
    """分位数对比：返回 (理论分位数, 样本分位数, 相对误差) 列表，供人工/报告核对。"""
    xs = sorted(_check_samples(samples))
    n = len(xs)
    if quantiles is None:
        quantiles = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
    rows = []
    for q in quantiles:
        theo = ppf(q)
        idx = min(max(int(q * n), 0), n - 1)
        samp = xs[idx]
        denom = abs(theo) if abs(theo) > 1e-12 else 1.0
        rows.append((q, theo, samp, abs(samp - theo) / denom))
    return rows


def _solve_linear(matrix, rhs):
    """高斯-约当消元（部分主元）解 n x n 线性方程组。"""
    n = len(matrix)
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-300:
            raise ValueError("singular covariance matrix in quantile test")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def quantile_test(samples, dist, quantiles=None, n_estimated=0, alpha=0.05):
    """分位数对比检验：样本分位数与理论分位数的联合渐近卡方检验。

    统计量 Q = z' Σ^{-1} z，其中 z_i = 样本q_i分位数 - ppf(q_i)，
    Σ_ij = (min(q_i,q_j) - q_i q_j) / (n f(t_i) f(t_j)) 为样本分位数的
    渐近协方差（f 为理论 pdf）。H0 下 Q 渐近服从 chi2(k)，k 为分位点个数。
    仅适用于连续分布（离散分布分位数有大量并列值，请用卡方检验）。
    """
    if dist.discrete:
        raise ValueError("quantile test is only defined for continuous "
                         "distributions; use chi_square_discrete for discrete ones")
    if not hasattr(dist, "pdf"):
        raise ValueError("quantile test requires the distribution to provide pdf()")
    xs = sorted(_check_samples(samples))
    n = len(xs)
    if quantiles is None:
        quantiles = (0.05, 0.25, 0.5, 0.75, 0.95)
    qs = list(quantiles)
    if not qs or any(not 0.0 < q < 1.0 for q in qs):
        raise ValueError("quantiles must be a non-empty sequence within (0, 1)")
    k = len(qs)
    theo = [dist.ppf(q) for q in qs]
    dens = [dist.pdf(t) for t in theo]
    if any(f <= 0.0 for f in dens):
        raise ValueError("theoretical quantile falls outside the distribution "
                         "support (pdf = 0); choose quantiles inside the support")
    z = []
    for q, t in zip(qs, theo):
        idx = min(max(int(q * n), 0), n - 1)
        z.append(xs[idx] - t)
    cov = [[(min(qi, qj) - qi * qj) / (n * fi * fj)
            for qj, fj in zip(qs, dens)] for qi, fi in zip(qs, dens)]
    y = _solve_linear(cov, z)
    stat = max(0.0, sum(zi * yi for zi, yi in zip(z, y)))
    df = k - n_estimated
    notes = ["asymptotic chi-square using the joint covariance of sample "
             "quantiles; reliable for n >= ~100"]
    if df < 1:
        return TestResult("quantile-comparison", stat, float("nan"), float("nan"),
                          df=df, alpha=alpha, conclusion="inconclusive",
                          notes=["degrees of freedom < 1 after accounting for "
                                 "estimated parameters"])
    if n_estimated:
        notes.append(f"{n_estimated} parameter(s) estimated from data; "
                     "df reduced accordingly (p-value is approximate)")
    crit = special.chi2_ppf(1.0 - alpha, df)
    p = special.gamma_q(df / 2.0, stat / 2.0)
    return TestResult("quantile-comparison", stat, crit, p, df=df, alpha=alpha,
                      conclusion="reject" if stat > crit else "accept",
                      notes=notes)


def goodness_of_fit(samples, dist, n_estimated=0, alpha=0.05):
    """统一入口：按分布类型与样本量自动选择卡方或 KS。"""
    xs = _check_samples(samples)
    n = len(xs)
    if dist.discrete:
        return chi_square_discrete(xs, dist.pmf, dist.support(),
                                   n_estimated=n_estimated, alpha=alpha)
    if n < SMALL_SAMPLE:
        result = ks_test(xs, dist.cdf, alpha=alpha,
                         params_estimated=n_estimated > 0)
        result.notes.append(f"n={n} < {SMALL_SAMPLE}: chi-square approximation "
                            "invalid for small samples, used KS test instead")
        return result
    return chi_square_continuous(xs, dist.cdf, dist.ppf,
                                 n_estimated=n_estimated, alpha=alpha)
