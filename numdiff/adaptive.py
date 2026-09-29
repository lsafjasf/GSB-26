"""自适应步长选择与误差估计。

理论背景（以中心差分为例）
--------------------------
一阶导三点中心格式:
    D(h) = f'(x) + f3(x) h^2 / 6 + O(h^4)        (截断误差 ~ C_t h^2)
    函数值扰动 eps_abs 导致的误差 ~ eps_abs / h    (舍入/噪声误差)
总误差 E(h) ≈ C_t h^2 + eps_abs / h 在
    h* = (3 eps_abs / |f3|)^(1/3)     (f3 = f 的三阶导)
处取最小，E* 的量级为 eps_abs^(2/3)。

二阶导三点中心格式:
    截断 ~ |f4| h^2 / 12, 扰动 ~ 4 eps_abs / h^2   (f4 = f 的四阶导)
    h* = (48 eps_abs / |f4|)^(1/4), E* 量级 eps_abs^(1/2)。

其中 eps_abs = max(机器舍入 eps_mach*|f|, 数据噪声 noise)。
关键结论：一阶导最优误差 ~ eps^(2/3)，二阶导 ~ eps^(1/2)。
噪声越大数值微分能给出的有效位数越少；当 E* 超过可容忍误差时
应停止使用数值微分（改用平滑/拟合后再微分，或解析/自动微分）。

自适应算法
----------
1. 探测：在较大探测步长 H ~ eps^(1/(q+2))（q 为被估计的高阶导数阶）
   上用 5 点模板估计 f3（一阶导情形）或 f4（二阶导情形）。
   探测步长必须显著大于最终步长，否则高阶导数估计本身被舍入/噪声淹没。
2. 由平衡公式算出 h*，迭代 2~3 次使 h 与高阶导数估计自洽。
3. 在 h* 处用中心格式计算导数；误差估计 = 截断项 + 扰动项，
   并与 h*/2 的 Richardson 差交叉验证，取较大者（保守）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .weights import fornberg_weights

_EPS = 2.220446049250313e-16  # 双精度机器 epsilon


@dataclass
class DiffResult:
    """数值微分结果。"""
    value: float             # 导数近似值
    error_est: float         # 总误差估计（截断 + 舍入/噪声，保守）
    h: float                 # 实际使用的步长
    n_evals: int             # 函数求值次数
    order: int               # 导数阶数（1 或 2）
    scheme: str              # 使用的格式
    trunc_est: float = 0.0   # 截断误差估计
    round_est: float = 0.0   # 舍入/噪声误差估计
    higher_deriv: float = 0.0  # 估计出的高阶导数（f3 或 f4）
    reliable: bool = True    # False 表示该噪声水平下数值微分不可信，应停止


def optimal_step_theory(order, f_abs, higher_deriv, eps_abs):
    """理论最优步长与最优误差（中心格式）。

    参数:
        order:        导数阶数 1 或 2
        f_abs:        |f(x)| 量级（保留接口对称，未直接使用）
        higher_deriv: |f3|（一阶导）或 |f4|（二阶导）的估计
        eps_abs:      函数值的绝对扰动水平

    返回:
        (h*, E*) 最优步长与最优总误差量级
    """
    hd = abs(higher_deriv)
    if hd <= 0.0 or eps_abs <= 0.0:
        return float("nan"), float("nan")
    if order == 1:
        h = (3.0 * eps_abs / hd) ** (1.0 / 3.0)
        e = hd * h * h / 6.0 + eps_abs / h
    elif order == 2:
        h = (48.0 * eps_abs / hd) ** 0.25
        e = hd * h * h / 12.0 + 4.0 * eps_abs / (h * h)
    else:
        raise ValueError("order 只支持 1 或 2")
    return h, e


def _stencil_eval(f, x0, h, m, offsets):
    """在偏移模板上用 Fornberg 权重求 m 阶导数，返回 (值, 求值次数)。"""
    nodes = [x0 + k * h for k in offsets]
    w = fornberg_weights(nodes, x0, m)
    return sum(wi * f(xi) for wi, xi in zip(w, nodes)), len(nodes)


def adaptive_derivative(f, x0, order=1, noise=0.0, h0=None, max_iter=3):
    """自适应步长的数值微分。

    参数:
        f:     可调用函数（可含噪声，噪声幅度用 noise 声明）
        x0:    求导点
        order: 1 或 2
        noise: 函数值的绝对噪声水平（0 表示仅机器舍入）
        h0:    初始探测步长（默认按 |x0| 与 eps 定标）

    返回:
        DiffResult
    """
    if order not in (1, 2):
        raise ValueError("order 只支持 1 或 2")
    x0 = float(x0)
    n_evals = 0

    f0 = f(x0)
    n_evals += 1
    # 函数值的有效扰动水平：机器舍入 ~ eps_mach * (|f| + 1)，加上数据噪声
    eps_abs = max(_EPS * (abs(f0) + 1.0), float(noise))

    # 步长下界：x0+h 必须能在浮点中与 x0 区分；上界：与 x 尺度相称
    x_scale = max(abs(x0), 1.0)
    h_min = 8.0 * _EPS * x_scale
    h_max = 0.25 * x_scale

    # 高阶导数估计模板：一阶导用 5 点估计 f3，二阶导用 5 点估计 f4
    m_high = order + 2
    offsets = (-2, -1, 0, 1, 2)

    # 探测步长 H：估计 q 阶导数时截断 ~ H^2、扰动 ~ eps/H^q，
    # 最优 H ~ eps^(1/(q+2))，显著大于最终步长 h*。
    h_probe = h0 if h0 else x_scale * eps_abs ** (1.0 / (m_high + 2.0))
    h_probe = min(max(h_probe, h_min), h_max)

    def probe(hp):
        """在探测步长 hp 上同时估计高阶导数与一阶导数（斜率）。

        返回 (高阶导数估计, 斜率估计, 高阶导数舍入下限, 斜率舍入下限)。
        斜率用于估计自变量舍入 eps*|x0| 传播到函数值的扰动
        |f'|*eps*|x0|，对大尺度自变量（如 x0=1e6）这一项主导。
        舍入下限 = eps_abs * sum|权重|，用于一致性判据的噪声地板。
        """
        nonlocal n_evals
        nodes = [x0 + k * hp for k in offsets]
        fvals = [f(xi) for xi in nodes]
        n_evals += len(nodes)
        w_high = fornberg_weights(nodes, x0, m_high)
        est = sum(wi * fi for wi, fi in zip(w_high, fvals))
        w_slope = fornberg_weights(nodes, x0, 1)
        slope = sum(wi * fi for wi, fi in zip(w_slope, fvals))
        floor_high = eps_abs * sum(abs(wi) for wi in w_high)
        floor_slope = eps_abs * sum(abs(wi) for wi in w_slope)
        return est, slope, floor_high, floor_slope

    # 探测步长收敛检查：若相邻两级探测的斜率/高阶导数估计不一致（超出
    # 各自噪声地板），说明 H 仍大于函数的局部变化尺度（如大自变量的
    # 振荡函数），将 H 除以 4 重试；要求连续两级一致才接受，避免振荡
    # 函数随机相位的偶然通过。
    # 收缩因子取非整数（~3.7），避免几何序列与周期函数发生相位混叠
    # （整数因子可能使 H mod 周期 保持很小，产生一致但错误的估计）。
    _SHRINK = 3.7
    est, slope, fl_h, fl_s = probe(h_probe)
    agree = 0
    for _ in range(16):
        if h_probe / _SHRINK < h_min or agree >= 2:
            break
        est2, slope2, fl_h2, fl_s2 = probe(h_probe / _SHRINK)
        tol_s = max(0.05 * abs(slope2), 3.0 * (fl_s + fl_s2))
        tol_h = max(0.10 * abs(est2), 3.0 * (fl_h + fl_h2))
        if abs(slope - slope2) <= tol_s and abs(est - est2) <= tol_h:
            agree += 1
        else:
            agree = 0
        est, slope, fl_h, fl_s = est2, slope2, fl_h2, fl_s2
        h_probe /= _SHRINK

    eps_abs = max(eps_abs, _EPS * x_scale * abs(slope), float(noise))

    higher = 0.0
    h = h_probe
    for _ in range(max_iter):
        if abs(est) > 1e-300:
            higher = est
            h_new, _ = optimal_step_theory(order, abs(f0), higher, eps_abs)
        else:
            # 高阶导数约为 0（如低次多项式）：用默认定标步长
            h_new = x_scale * eps_abs ** (1.0 / (order + 2.0))
        h_new = min(max(h_new, h_min), h_max)
        if abs(h_new - h) < 0.2 * h:
            h = h_new
            break
        h = h_new

    # 在 h* 处计算导数（中心格式）
    if order == 1:
        val, k = _stencil_eval(f, x0, h, 1, (-1, 0, 1))
        trunc = abs(higher) * h * h / 6.0
        rnd = eps_abs / h
    else:
        val, k = _stencil_eval(f, x0, h, 2, (-1, 0, 1))
        trunc = abs(higher) * h * h / 12.0
        rnd = 4.0 * eps_abs / (h * h)
    n_evals += k

    # Richardson 交叉验证：h/2 处再算一次，差值估计截断误差
    h2 = h / 2.0
    if h2 >= h_min:
        val2, k = _stencil_eval(f, x0, h2, order, (-1, 0, 1))
        n_evals += k
        rich = abs(val - val2) / 3.0  # O(h^2) 格式的外推修正量级
    else:
        rich = 0.0

    error_est = max(trunc + rnd, rich + rnd, 1e-300)

    return DiffResult(
        value=val,
        error_est=error_est,
        h=h,
        n_evals=n_evals,
        order=order,
        scheme="central2" if order == 1 else "central2_d2",
        trunc_est=trunc,
        round_est=rnd,
        higher_deriv=higher,
    )
