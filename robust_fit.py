"""robust_fit.py -- 稳健曲线拟合库（仅依赖 Python 标准库）。

方法概述
--------
- 普通最小二乘 (OLS)：通过【带列主元的 Householder QR 分解】求解最小二乘，
  从不构造正规方程 X^T X，也不对任何矩阵显式求逆。
- 稳健拟合 (IRLS)：迭代重加权最小二乘，支持 Huber 与 Tukey bisquare 损失。
  尺度用 MAD（中位数绝对偏差）估计：sigma = 1.4826 * MAD，
  1.4826 使该估计在高斯噪声下对标准差一致。
  Huber 阈值 c=1.345（高斯噪声下 95% 渐近效率的经典取值）；
  bisquare 阈值 c=4.685（同为 95% 效率的经典取值）。
- 条件数估计：对 QR 得到的三角因子 R（cond2(X) == cond2(R)）做
  幂迭代（最大奇异值）与逆迭代（最小奇异值），得到 cond2 的估计，
  全程只做三角回代，无显式求逆。
- 秩亏处理：列主元 QR 按容差判秩，丢弃线性相关列（系数置 0 并给出警告），
  因此对完全共线的设计矩阵有确定行为。

边界行为
--------
- 完全共线：检出秩亏，相关列系数置 0，warnings 中说明，cond 报告为 inf。
- 无噪声：残差为 0 时 MAD=0，尺度回退到机器精度级小量，权重全为 1，
  IRLS 退化为 OLS，正常收敛。
- 单点 / 欠定：n < p 时按秩求解可识别部分，warnings 中说明欠定。
- 全部为离群点：超过崩溃点（~50%）的污染在原则上无法被任何稳健方法
  识别。本库的确定行为：拟合照常收敛并返回结果；若超过半数样本被降权，
  在 warnings 中明确提示结果不可信。两种典型情形：
    * 同向污染（所有点同方向偏移）：被截距等参数整体吸收，拟合"成功"，
      残差很小，但参数偏离真值——这是不可检测的情形；
    * 异向污染：MAD 尺度被污染撑大，稳健方法退化为接近 OLS，
      sigma 与残差统计会显著变大，可作为事后诊断信号。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

_EPS = 2.220446049250313e-16  # float64 机器精度


# ---------------------------------------------------------------------------
# 基础线性代数（全部基于列表，无第三方依赖）
# ---------------------------------------------------------------------------

def _median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _qr_cp(A):
    """带列主元的 Householder QR 分解。

    返回 (Qt, R, piv, rank)：
      A[:, piv] = Q R，Qt = Q^T 为 m x m 正交矩阵（按 H_k...H_1 累积），
      R 为 m x n 上梯形矩阵，
      rank 按容差 max(m,n)*eps*||A||_F 判定。
    """
    m = len(A)
    n = len(A[0])
    R = [row[:] for row in A]
    Qt = [[1.0 if i == j else 0.0 for j in range(m)] for i in range(m)]
    piv = list(range(n))

    norm_f = math.sqrt(sum(v * v for row in A for v in row))
    tol = max(m, n) * _EPS * (norm_f if norm_f > 0.0 else 1.0)

    rank = 0
    for k in range(min(m, n)):
        # 列主元：在剩余列中选 k..m-1 行范数最大的列
        best, best_norm = k, -1.0
        for j in range(k, n):
            cn = math.sqrt(sum(R[i][j] ** 2 for i in range(k, m)))
            if cn > best_norm:
                best, best_norm = j, cn
        if best_norm <= tol:
            break  # 剩余列在数值上全为零 -> 秩亏
        if best != k:
            for i in range(m):
                R[i][k], R[i][best] = R[i][best], R[i][k]
            piv[k], piv[best] = piv[best], piv[k]

        # Householder 变换消去第 k 列对角线以下元素
        x = [R[i][k] for i in range(k, m)]
        alpha = math.sqrt(sum(v * v for v in x))
        if x[0] >= 0.0:
            alpha = -alpha
        v = x[:]
        v[0] -= alpha
        vv = sum(t * t for t in v)
        if vv > 0.0:
            beta = 2.0 / vv
            for j in range(k, n):
                s = sum(v[i - k] * R[i][j] for i in range(k, m))
                for i in range(k, m):
                    R[i][j] -= beta * s * v[i - k]
            for j in range(m):  # 同时累积 Qt <- H * Qt（左乘）
                s = sum(v[i - k] * Qt[i][j] for i in range(k, m))
                for i in range(k, m):
                    Qt[i][j] -= beta * s * v[i - k]
        R[k][k] = alpha
        for i in range(k + 1, m):
            R[i][k] = 0.0
        rank += 1
    return Qt, R, piv, rank


def _back_subst(R, b, r):
    """解上三角系统 R[0:r,0:r] x = b[0:r]（回代，非求逆）。"""
    x = [0.0] * r
    for i in range(r - 1, -1, -1):
        s = b[i] - sum(R[i][j] * x[j] for j in range(i + 1, r))
        x[i] = s / R[i][i]
    return x


def _forward_subst(L, b, r):
    """解下三角系统（用于逆迭代中的 R^T）。"""
    x = [0.0] * r
    for i in range(r):
        s = b[i] - sum(L[i][j] * x[j] for j in range(i))
        x[i] = s / L[i][i]
    return x


def _cond2_estimate(R, r, iters=200):
    """估计 r 阶上三角因子 R 的 2-范数条件数。

    cond2(X) == cond2(R)。最大奇异值用幂迭代（作用于 R^T R），
    最小奇异值用逆迭代（每步只做两次三角回代）。
    """
    if r == 0:
        return math.inf
    RtR_diag_min = min(abs(R[i][i]) for i in range(r))
    if RtR_diag_min == 0.0:
        return math.inf

    def matvec_RTR(x):
        y = [sum(R[i][j] * x[j] for j in range(i, r)) for i in range(r)]
        return [sum(R[j][i] * y[j] for j in range(i + 1)) for i in range(r)]

    x = [1.0 / math.sqrt(r)] * r
    lam_max = 0.0
    for _ in range(iters):
        y = matvec_RTR(x)
        ny = math.sqrt(sum(v * v for v in y))
        if ny == 0.0:
            break
        x = [v / ny for v in y]
        lam_max = ny

    # 逆迭代：反复解 R^T w = x, R z = w
    z = [1.0 / math.sqrt(r)] * r
    lam_min = 0.0
    for _ in range(iters):
        w = _forward_subst(R, z, r)
        z = _back_subst(R, w, r)
        nz = math.sqrt(sum(v * v for v in z))
        if nz == 0.0:
            return math.inf
        z = [v / nz for v in z]
        lam_min = 1.0 / nz

    if lam_min <= 0.0:
        return math.inf
    return math.sqrt(lam_max / lam_min)


def _lstsq(X, y):
    """最小二乘核心：返回 (coef, rank, piv, cond, R)。

    通过列主元 QR 求解；秩亏时仅解可识别部分，其余系数置 0。
    """
    m = len(X)
    n = len(X[0])
    Qt, R, piv, rank = _qr_cp(X)
    qty = [sum(Qt[k][i] * y[i] for i in range(m)) for k in range(m)]
    cond = _cond2_estimate(R, rank) if rank == n else math.inf
    coef_p = [0.0] * n
    if rank > 0:
        sol = _back_subst(R, qty, rank)
        for i in range(rank):
            coef_p[i] = sol[i]
    coef = [0.0] * n
    for pos, col in enumerate(piv):
        coef[col] = coef_p[pos]
    return coef, rank, piv, cond


# ---------------------------------------------------------------------------
# 权重函数（损失函数的 psi(u)/u）
# ---------------------------------------------------------------------------

def _huber_weight(u, c):
    a = abs(u)
    return 1.0 if a <= c else c / a


def _bisquare_weight(u, c):
    a = abs(u)
    if a >= c:
        return 0.0
    t = 1.0 - (u / c) ** 2
    return t * t


_WEIGHTS = {"huber": _huber_weight, "bisquare": _bisquare_weight}
_DEFAULT_C = {"huber": 1.345, "bisquare": 4.685}


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------

@dataclass
class FitResult:
    method: str
    params: list
    residuals: list
    weights: list
    outliers: list
    r_squared: float
    sigma: float                 # 稳健尺度估计 (1.4826*MAD)
    condition_number: float
    rank: int
    n_params: int
    n_iter: int
    converged: bool
    residual_stats: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def summary(self):
        lines = [
            f"method            : {self.method}",
            f"params            : {[round(p, 6) for p in self.params]}",
            f"r_squared         : {self.r_squared:.6f}",
            f"sigma (MAD-based) : {self.sigma:.6g}",
            f"cond2 (estimate)  : {self.condition_number:.6g}",
            f"rank / n_params   : {self.rank} / {self.n_params}",
            f"iterations        : {self.n_iter} (converged={self.converged})",
            f"outliers flagged  : {sum(self.outliers)} / {len(self.outliers)}",
        ]
        for k, v in self.residual_stats.items():
            lines.append(f"resid.{k:<12}: {v:.6g}")
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def _residual_stats(residuals):
    n = len(residuals)
    mean = sum(residuals) / n
    var = sum((r - mean) ** 2 for r in residuals) / max(n - 1, 1)
    med = _median(residuals)
    mad = _median([abs(r - med) for r in residuals])
    return {
        "mean": mean,
        "std": math.sqrt(var),
        "median": med,
        "mad": mad,
        "max_abs": max(abs(r) for r in residuals),
    }


def _r_squared(y, residuals):
    mean_y = sum(y) / len(y)
    ss_tot = sum((v - mean_y) ** 2 for v in y)
    ss_res = sum(r * r for r in residuals)
    if ss_tot == 0.0:
        return 1.0 if ss_res == 0.0 else 0.0
    return 1.0 - ss_res / ss_tot


def fit(X, y, method="irls", loss="huber", c=None, max_iter=100, tol=1e-10):
    """拟合线性模型 y = X @ params。

    参数
    ----
    X      : m x n 设计矩阵（每行一个样本）
    y      : 长度 m 的响应
    method : "ols" 或 "irls"
    loss   : "huber" 或 "bisquare"（仅 irls 有效）
    c      : 调优常数，默认 Huber=1.345 / bisquare=4.685（95% 高斯效率）
    """
    m = len(X)
    if m == 0:
        raise ValueError("empty dataset: X has no rows")
    n = len(X[0])
    if len(y) != m:
        raise ValueError("X and y size mismatch")
    if any(len(row) != n for row in X):
        raise ValueError("X rows have inconsistent lengths")
    if loss not in _WEIGHTS:
        raise ValueError(f"unknown loss: {loss}")
    if c is None:
        c = _DEFAULT_C[loss]

    warnings = []
    if m < n:
        warnings.append(
            f"underdetermined system: {m} samples < {n} parameters; "
            "solution identifies only the estimable part")

    if method == "ols":
        coef, rank, _, cond = _lstsq(X, y)
        residuals = [y[i] - sum(X[i][j] * coef[j] for j in range(n))
                     for i in range(m)]
        stats = _residual_stats(residuals)
        sigma = 1.4826 * stats["mad"]
        weights = [1.0] * m
        outliers = [False] * m
        n_iter, converged = 1, True
    elif method == "irls":
        coef, rank, _, cond = _lstsq(X, y)
        wfun = _WEIGHTS[loss]
        weights = [1.0] * m
        converged = False
        n_iter = 0
        for n_iter in range(1, max_iter + 1):
            residuals = [y[i] - sum(X[i][j] * coef[j] for j in range(n))
                         for i in range(m)]
            med = _median(residuals)
            mad = _median([abs(r - med) for r in residuals])
            # 无噪声/近无噪声数据：MAD=0，回退到极小尺度，权重保持为 1
            sigma = 1.4826 * mad
            if sigma <= 1e-12:
                sigma = 1e-12
            weights = [wfun((r - med) / sigma, c) for r in residuals]
            sw = [math.sqrt(w) for w in weights]
            Xw = [[sw[i] * X[i][j] for j in range(n)] for i in range(m)]
            yw = [sw[i] * y[i] for i in range(m)]
            new_coef, rank, _, cond = _lstsq(Xw, yw)
            delta = max((abs(a - b) for a, b in zip(new_coef, coef)),
                        default=0.0)
            scale = max(1.0, max(abs(v) for v in new_coef))
            coef = new_coef
            if delta <= tol * scale:
                converged = True
                break
        residuals = [y[i] - sum(X[i][j] * coef[j] for j in range(n))
                     for i in range(m)]
        stats = _residual_stats(residuals)
        sigma = max(1.4826 * stats["mad"], 1e-12)
        # 离群标记阈值：|u|>2.5（Huber 权重<0.538），避免把高斯尾部
        # 的正常点（|u|>1.345 约占 18%）误报为离群点
        outliers = [w < 0.5 for w in weights]
        frac_down = sum(outliers) / m
        if frac_down > 0.5:
            warnings.append(
                f"{frac_down:.0%} of samples down-weighted: contamination "
                "exceeds the ~50% breakdown point; robust fit is NOT "
                "trustworthy (possible all-outlier data)")
    else:
        raise ValueError(f"unknown method: {method}")

    if rank < n:
        warnings.append(
            f"rank-deficient design matrix: rank {rank} < {n} columns; "
            "linearly dependent columns got coefficient 0 (collinearity)")

    return FitResult(
        method=method if method == "ols" else f"irls/{loss}(c={c})",
        params=coef,
        residuals=residuals,
        weights=weights,
        outliers=outliers,
        r_squared=_r_squared(y, residuals),
        sigma=sigma,
        condition_number=cond,
        rank=rank,
        n_params=n,
        n_iter=n_iter,
        converged=converged,
        residual_stats=_residual_stats(residuals),
        warnings=warnings,
    )


def poly_design(x, degree):
    """由自变量序列构造多项式设计矩阵 [1, x, x^2, ...]。"""
    return [[xi ** k for k in range(degree + 1)] for xi in x]


def fit_polynomial(x, y, degree, **kw):
    """多项式曲线拟合的便捷接口。"""
    return fit(poly_design(x, degree), list(y), **kw)
