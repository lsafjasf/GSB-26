"""稳健曲线拟合：普通最小二乘 (OLS) 与迭代重加权最小二乘 (IRLS)。

稳健方法说明
------------
IRLS 在每一步用当前残差计算权重，把问题转化为加权最小二乘，
再用 Householder QR 求解。支持两种经典损失函数：

- Huber:    rho(u) = u^2/2            (|u| <= c)
                     c|u| - c^2/2      (|u| >  c),   c = 1.345
  权重 w(u) = min(1, c/|u|)。对应 95% 渐近效率（正态假设下）。
  离群点仍保留部分影响，击穿点高但影响函数无界回弹慢。

- Tukey 双权 (bisquare):
            rho(u) 分段三次，权重 w(u) = (1 - (u/c)^2)^2  (|u| <= c)
                                     0                     (|u| >  c),  c = 4.685
  权重可降到 0，能完全剔除离群点（软截断），同样为 95% 渐近效率。
  是本库默认方法。

阈值依据：u = r / s 是标准化残差，尺度 s 用 MAD 估计
    s = median(|r - median(r)|) / 0.6745
0.6745 是标准正态的 0.75 分位数，使 s 在正态噪声下是 sigma 的一致估计。
常数 1.345 / 4.685 是 Huber/Tukey 文献中达到 95% 正态效率的标准取值。

边界行为（明确约定）
--------------------
- 完全共线：QR 中检测到秩亏，抛出 RankDeficientError（指明列号）。
- 无噪声：残差尺度 s 为 0，IRLS 退化为 OLS 并立即收敛，不除零。
- 单点数据：参数个数 <= 1 时可精确拟合；否则抛出 UnderdeterminedError。
- 全部/过半为离群点：稳健方法已超击穿点（50%），仍返回结果，
  但在 result.warnings 中明确警告拟合不可信。
"""

import math
from dataclasses import dataclass, field

from .linalg import qr_solve, cond_estimate, RankDeficientError  # noqa: F401

TINY = 1e-14  # 判定"无噪声"的残差尺度阈值


class UnderdeterminedError(Exception):
    """样本数少于参数个数时抛出。"""


def poly_design(xs, degree):
    """构造多项式设计矩阵：每行为 [1, x, x^2, ..., x^degree]。"""
    rows = []
    for x in xs:
        row = [1.0]
        for _ in range(degree):
            row.append(row[-1] * x)
        rows.append(row)
    return rows


def _median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def mad_scale(residuals):
    """MAD 稳健尺度估计：median|r - median(r)| / 0.6745。"""
    med = _median(residuals)
    return _median([abs(r - med) for r in residuals]) / 0.6745


def _weights(loss, u):
    """按损失函数由标准化残差 u 计算 IRLS 权重。"""
    if loss == "huber":
        c = 1.345
        return [1.0 if abs(t) <= c else c / abs(t) for t in u]
    if loss == "tukey":
        c = 4.685
        out = []
        for t in u:
            if abs(t) >= c:
                out.append(0.0)
            else:
                q = 1.0 - (t / c) ** 2
                out.append(q * q)
        return out
    raise ValueError(f"未知损失函数: {loss!r}，可选 'huber' / 'tukey'")


@dataclass
class FitResult:
    """拟合结果：参数 + 可信度说明。"""
    method: str
    beta: list                      # 拟合参数
    residuals: list                 # 每个样本的残差
    weights: list                   # 每个样本的最终权重（OLS 全为 1）
    outliers: list                  # 每个样本是否被标记为离群（权重==0）
    r_squared: float                # 决定系数
    adj_r_squared: float            # 调整决定系数
    resid_stats: dict               # 残差统计
    cond_number: float              # 设计矩阵条件数估计 cond_2
    n_iter: int                     # IRLS 迭代次数（OLS 为 1）
    converged: bool
    warnings: list = field(default_factory=list)

    def summary(self, max_weight_rows=8):
        lines = [
            f"方法            : {self.method}",
            f"参数 beta       : {[round(b, 6) for b in self.beta]}",
            f"R^2 / 调整R^2   : {self.r_squared:.6f} / {self.adj_r_squared:.6f}",
            f"条件数 cond_2(A): {self.cond_number:.3e}",
            f"迭代次数/收敛   : {self.n_iter} / {self.converged}",
            "残差统计        : " + ", ".join(
                f"{k}={v:.4g}" for k, v in self.resid_stats.items()),
            f"离群点数量      : {sum(self.outliers)} / {len(self.outliers)}",
        ]
        for w in self.warnings:
            lines.append(f"警告            : {w}")
        lines.append("样本权重(前若干): "
                     + ", ".join(f"{w:.3f}" for w in self.weights[:max_weight_rows])
                     + (" ..." if len(self.weights) > max_weight_rows else ""))
        return "\n".join(lines)


def _resid_stats(residuals):
    n = len(residuals)
    mean = sum(residuals) / n
    var = sum((r - mean) ** 2 for r in residuals) / max(n - 1, 1)
    return {
        "mean": mean,
        "std": math.sqrt(var),
        "mad_scale": mad_scale(residuals),
        "max_abs": max(abs(r) for r in residuals),
    }


def _weighted_lstsq(design, y, weights):
    """加权最小二乘：对 sqrt(w) 加权后的矩阵做 QR，不求逆、不用正规方程。"""
    a = []
    b = []
    for row, yi, wi in zip(design, y, weights):
        sw = math.sqrt(wi)
        a.append([v * sw for v in row])
        b.append(yi * sw)
    beta, r, vs = qr_solve(a, b)
    return beta, r


def _fit(design, y, method, loss, max_iter, tol):
    m = len(design)
    n = len(design[0])
    if m < n:
        raise UnderdeterminedError(
            f"样本数 {m} 少于参数个数 {n}，问题欠定，无法唯一拟合。"
            f"请增加样本或降低模型阶数。")

    warnings = []
    weights = [1.0] * m
    beta = None
    beta_prev = None
    r = None
    n_iter = 0
    converged = True

    for it in range(1, max_iter + 1):
        n_iter = it
        beta_new, r = _weighted_lstsq(design, y, weights)
        residuals = [yi - sum(row[j] * beta_new[j] for j in range(n))
                     for row, yi in zip(design, y)]
        if method == "ols":
            beta = beta_new
            break
        # IRLS：MAD 尺度 + 损失函数权重
        s = mad_scale(residuals)
        if s < TINY:
            # 无噪声（或残差已机器精度为零）：权重无意义，按收敛处理
            beta = beta_new
            warnings.append("残差尺度≈0（无噪声数据），IRLS 退化为 OLS。")
            break
        weights = _weights(loss, [ri / s for ri in residuals])
        if beta_prev is not None:
            shift = math.sqrt(sum((a - b) ** 2 for a, b in zip(beta_new, beta_prev)))
            scale = math.sqrt(sum(b * b for b in beta_new)) or 1.0
            if shift / scale < tol:
                beta = beta_new
                break
        beta_prev = beta_new
        beta = beta_new
    else:
        converged = False
        warnings.append(f"IRLS 在 {max_iter} 次迭代内未收敛，返回当前结果。")

    residuals = [yi - sum(row[j] * beta[j] for j in range(n))
                 for row, yi in zip(design, y)]
    outliers = [w == 0.0 for w in weights]
    n_out = sum(outliers)
    if method != "ols" and n_out > m / 2:
        warnings.append(
            f"{n_out}/{m} 样本被标记为离群（>50%，已超稳健方法击穿点），"
            f"拟合结果不可信：可能大部分数据都是离群点。")

    y_mean = sum(y) / m
    ss_res = sum(ri * ri for ri in residuals)
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    adj = 1.0 - (1.0 - r2) * (m - 1) / max(m - n, 1) if m > n else float("nan")
    if r2 < 0.1:
        warnings.append(
            f"决定系数 R^2={r2:.4f} 极低，数据中可能不存在可拟合结构"
            f"（例如全部样本都是离群点），参数不可信。")

    return FitResult(
        method=f"{method}" + (f"({loss})" if method == "irls" else ""),
        beta=beta,
        residuals=residuals,
        weights=weights,
        outliers=outliers,
        r_squared=r2,
        adj_r_squared=adj,
        resid_stats=_resid_stats(residuals),
        cond_number=cond_estimate(r, n),
        n_iter=n_iter,
        converged=converged,
        warnings=warnings,
    )


def ols_fit(design, y):
    """普通最小二乘。design 为 m x n 设计矩阵（如 poly_design 的输出）。"""
    return _fit(design, y, method="ols", loss=None, max_iter=1, tol=0.0)


def robust_fit(design, y, loss="tukey", max_iter=50, tol=1e-10):
    """IRLS 稳健拟合，loss 可选 'tukey'（默认）或 'huber'。"""
    return _fit(design, y, method="irls", loss=loss, max_iter=max_iter, tol=tol)
