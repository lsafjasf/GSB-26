"""含噪数据的偏差—方差权衡、最佳步长搜索与自适应停止。

理论框架
--------
对截断阶 p、导数阶 m 的差分格式（等距步长 h），记 ŵ 为单位步长权重：

    偏差（截断误差）  bias(h) = C · |f^{(p+m)}| · h^p,  C = |Σ ŵᵢ kᵢ^(p+m)| / (p+m)!
    标准差（噪声传播） std(h)  = σ · G / h^m,            G = sqrt(Σ ŵᵢ²)
    总误差            E(h)    = sqrt(bias² + std²)

bias 随 h 增大、std 随 h 减小，二者权衡（bias–variance tradeoff）给出
唯一最优步长 h*。噪声 σ 越大，h* 越大、最优可能误差 E* 越高：
一阶导 E* ~ σ^(2/3)，二阶导 E* ~ σ^(1/2)（三点中心格式）。

本模块提供：
- scheme_error_coeffs / error_model: 上述模型的系数与曲线求值
- search_best_step:    在几何网格上搜索使模型总误差最小的步长
- bias_variance_curve: 对含噪数据做蒙特卡洛偏差/方差分解（验证模型）
- adaptive_noisy_derivative: 带自适应停止条件的含噪微分——沿 h 递减
  方向扫描，误差估计开始回升即停；若最优可能误差仍超过 tol，
  返回 reliable=False（应停止数值微分，改用平滑/拟合后微分）
"""

from __future__ import annotations

import math
from collections import namedtuple

from .adaptive import _EPS, DiffResult, adaptive_derivative
from .schemes import SCHEMES
from .weights import fornberg_weights

# 模型曲线上的一个采样点 / 蒙特卡洛分解的一行
CurvePoint = namedtuple("CurvePoint", "h bias std total")
BVRow = namedtuple("BVRow", "h bias std rmse model_bias model_std model_total")


def scheme_error_coeffs(scheme):
    """计算格式的误差模型系数。

    返回:
        (m, p, C, G): 导数阶、截断阶、截断系数、噪声增益。
        C 由矩条件直接计算：C = |Σ ŵᵢ kᵢ^(p+m)| / (p+m)!，
        无需查表，对 SCHEMES 中任何格式（含非对称）都成立。
    """
    m, offsets, p, _ = SCHEMES[scheme]
    w = fornberg_weights([float(k) for k in offsets], 0.0, m)
    q = p + m
    moment = abs(sum(wi * k ** q for wi, k in zip(w, offsets)))
    c_trunc = moment / math.factorial(q)
    g_noise = math.sqrt(sum(wi * wi for wi in w))
    return m, p, c_trunc, g_noise


def error_model(h, scheme, noise_std, higher_deriv):
    """偏差—方差模型在步长 h 处的求值。

    参数:
        h:            步长
        scheme:       SCHEMES 中的格式名
        noise_std:    函数值噪声的标准差
        higher_deriv: |f^{(p+m)}| 的估计（截断误差的主导系数）

    返回:
        (bias, std, total): total = sqrt(bias² + std²)
    """
    m, p, c, g = scheme_error_coeffs(scheme)
    bias = c * abs(higher_deriv) * h ** p
    std = noise_std * g / h ** m
    return bias, std, math.hypot(bias, std)


def estimate_higher_derivative(f, x0, q, eps_abs, x_scale=None, h0=None):
    """在探测步长上估计 q 阶导数（截断误差模型的主导系数）。

    探测步长 H ~ eps^(1/(q+2))（估计 q 阶导数时截断 ~ H²、扰动
    ~ eps/H^q 的平衡点），显著大于最终微分步长。若相邻两级探测
    估计不一致（超出噪声地板），将 H 除以 3.7 重试，与 adaptive
    模块同策略。返回 (估计值, 函数求值次数)。
    """
    x_scale = x_scale if x_scale else max(abs(x0), 1.0)
    count = q + 1 if (q + 1) % 2 == 1 else q + 2
    half = count // 2
    offsets = tuple(range(-half, half + 1))
    h_min = 8.0 * _EPS * x_scale
    h_max = 0.25 * x_scale
    h = h0 if h0 else x_scale * eps_abs ** (1.0 / (q + 2.0))
    h = min(max(h, h_min), h_max)
    n_evals = 0

    def probe(hp):
        nonlocal n_evals
        nodes = [x0 + k * hp for k in offsets]
        fvals = [f(xi) for xi in nodes]
        n_evals += len(nodes)
        w = fornberg_weights(nodes, x0, q)
        est = sum(wi * fi for wi, fi in zip(w, fvals))
        floor = eps_abs * sum(abs(wi) for wi in w)
        return est, floor

    est, floor = probe(h)
    agree = 0
    for _ in range(12):
        if h / 3.7 < h_min or agree >= 1:
            break
        est2, floor2 = probe(h / 3.7)
        if abs(est - est2) <= max(0.1 * abs(est2), 3.0 * (floor + floor2)):
            agree += 1
        else:
            agree = 0
        est, floor = est2, floor2
        h /= 3.7
    return est, n_evals


def _geomspace(lo, hi, n):
    if n == 1:
        return [lo]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [lo * ratio ** i for i in range(n)]


# 最佳步长搜索结果
StepSearch = namedtuple("StepSearch",
                        "h_best e_best curve higher_deriv n_evals scheme")


def search_best_step(f, x0, order, noise=0.0, scheme=None, n_grid=121):
    """搜索使偏差—方差模型总误差最小的步长。

    参数:
        f:      可调用函数（可含噪声，噪声标准差用 noise 声明）
        x0:     求导点
        order:  导数阶数 1 或 2
        noise:  函数值噪声标准差（0 表示仅机器舍入）
        scheme: 差分格式名（默认 central2 / central2_d2）
        n_grid: 几何网格点数

    返回:
        StepSearch(h_best, e_best, curve, higher_deriv, n_evals, scheme)
        curve 为 CurvePoint 列表（h 升序），即偏差—方差权衡曲线。
    """
    if scheme is None:
        scheme = "central2" if order == 1 else "central2_d2"
    m, p, _, _ = scheme_error_coeffs(scheme)
    x0 = float(x0)
    f0 = f(x0)
    eps_abs = max(_EPS * (abs(f0) + 1.0), float(noise))
    x_scale = max(abs(x0), 1.0)
    h_min = 8.0 * _EPS * x_scale
    h_max = 0.25 * x_scale

    higher, n_evals = estimate_higher_derivative(f, x0, p + m, eps_abs, x_scale)
    if not math.isfinite(higher) or abs(higher) < 1e-300:
        higher = 1e-300  # 高阶导数约为 0：截断项消失，最优步长趋于上界

    curve = []
    for h in _geomspace(h_min, h_max, n_grid):
        bias, std, total = error_model(h, scheme, eps_abs, higher)
        curve.append(CurvePoint(h, bias, std, total))
    best = min(curve, key=lambda pt: pt.total)
    return StepSearch(best.h, best.total, curve, higher, n_evals + 1, scheme)


def adaptive_noisy_derivative(f, x0, order=1, noise=0.0, tol=None, scheme=None):
    """带自适应停止条件的含噪数值微分。

    自适应停止条件：沿步长从大到小扫描偏差—方差模型的误差估计，
    估计开始回升即停——继续缩小步长只会放大噪声，不会改善结果。
    若最优可能误差仍超过 tol，返回 reliable=False：该噪声水平下
    数值微分无法达到要求精度，应停止并改用平滑/拟合后微分。

    参数:
        f:      可调用函数（可含噪声）
        x0:     求导点
        order:  1 或 2
        noise:  函数值噪声标准差
        tol:    可容忍的绝对误差（None 表示不检查，仅给出估计）
        scheme: 差分格式名（默认 central2 / central2_d2）

    返回:
        DiffResult（error_est 为误差量级估计，reliable 为停止结论）
    """
    if scheme is None:
        scheme = "central2" if order == 1 else "central2_d2"
    search = search_best_step(f, x0, order, noise, scheme)

    # 自适应停止：curve 按 h 升序，从最大 h 端向小扫描，首次回升即停
    best = search.curve[-1]
    for pt in reversed(search.curve):
        if pt.total > best.total:
            break  # 误差估计回升 → 停止，best 即最优点
        best = pt

    # 在最优步长处取值
    m, offsets, _, _ = SCHEMES[scheme]
    nodes = [x0 + k * best.h for k in offsets]
    w = fornberg_weights(nodes, x0, m)
    val = sum(wi * f(xi) for wi, xi in zip(w, nodes))
    n_evals = search.n_evals + len(nodes)

    error_est = max(best.total, 1e-300)
    reliable = tol is None or error_est <= tol
    return DiffResult(
        value=val,
        error_est=error_est,
        h=best.h,
        n_evals=n_evals,
        order=order,
        scheme=scheme,
        trunc_est=best.bias,
        round_est=best.std,
        higher_deriv=search.higher_deriv,
        reliable=reliable,
    )


def bias_variance_curve(f, x0, order, noise, hs, n_trials=25, scheme=None,
                        exact=None, seed=20260929):
    """对含噪数据做蒙特卡洛偏差—方差分解，并与理论模型对照。

    对每个步长 h 做 n_trials 次独立试验（每次向干净函数注入
    N(0, noise²) 高斯噪声），得到估计值的偏差、标准差与 RMSE；
    同时给出 error_model 的理论预测，用于验证模型正确性。

    参数:
        f:        干净的可调用函数（噪声由本函数注入，保证可复跑）
        x0:       求导点
        order:    1 或 2
        noise:    注入噪声的标准差
        hs:       步长列表
        n_trials: 每个步长的试验次数
        scheme:   差分格式名（默认 central2 / central2_d2）
        exact:    精确导数值（默认用干净函数自适应微分代替）
        seed:     随机种子（固定，保证可复跑）

    返回:
        BVRow 列表（h 与 hs 一一对应）
    """
    import random

    if scheme is None:
        scheme = "central2" if order == 1 else "central2_d2"
    if exact is None:
        exact = adaptive_derivative(f, x0, order=order).value
    m, offsets, p, _ = SCHEMES[scheme]
    x0 = float(x0)
    f0 = f(x0)
    eps_abs = max(_EPS * (abs(f0) + 1.0), float(noise))
    higher, _ = estimate_higher_derivative(f, x0, p + m, eps_abs)

    rng = random.Random(seed)
    rows = []
    for h in hs:
        vals = []
        for _ in range(n_trials):
            nodes = [x0 + k * h for k in offsets]
            w = fornberg_weights(nodes, x0, m)
            acc = 0.0
            for wi, xi in zip(w, nodes):
                acc += wi * (f(xi) + rng.gauss(0.0, noise))
            vals.append(acc)
        mean = sum(vals) / n_trials
        var = sum((v - mean) ** 2 for v in vals) / (n_trials - 1)
        bias = abs(mean - exact)
        std = math.sqrt(var)
        rmse = math.sqrt(sum((v - exact) ** 2 for v in vals) / n_trials)
        mb, ms, mt = error_model(h, scheme, noise, higher)
        rows.append(BVRow(h, bias, std, rmse, mb, ms, mt))
    return rows
