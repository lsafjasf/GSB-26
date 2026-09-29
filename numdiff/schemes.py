"""命名差分格式与网格微分。

每种格式用相对步长 h 的整数偏移定义，通过 Fornberg 权重计算系数，
因此等距/非等距、内部/边界点使用同一套代码。

理论截断误差阶（等距网格、光滑函数）：
- forward1/backward1 : O(h)    一阶导，2 点单侧
- central2           : O(h^2)  一阶导，3 点中心
- forward2/backward2 : O(h^2)  一阶导，3 点单侧（边界用）
- forward3/backward3 : O(h^3)  一阶导，4 点单侧（边界高精度）
- central4           : O(h^4)  一阶导，5 点中心
- forward4/backward4 : O(h^4)  一阶导，5 点单侧（边界高精度）
- central6           : O(h^6)  一阶导，7 点中心
- central2_d2        : O(h^2)  二阶导，3 点中心
- forward2_d2/backward2_d2 : O(h^2) 二阶导，4 点单侧（边界用）
- forward3_d2/backward3_d2 : O(h^3) 二阶导，5 点单侧（边界高精度）
- central4_d2        : O(h^4)  二阶导，5 点中心
- central6_d2        : O(h^6)  二阶导，7 点中心

注：n 点单侧公式对 m 阶导数的截断阶为 p = n - m（矩条件 j=m+1..n-1
全部消失）；中心对称公式因奇次矩自动抵消再高一阶。所有阶数均由
tests 与 experiments/order_verification.py 用"h 缩小 10 倍、误差缩小
10^p 倍"的比例关系数值验证。
"""

from __future__ import annotations

from .weights import fornberg_weights

# name -> (导数阶 m, 相对偏移元组, 截断误差阶 p, 说明)
SCHEMES = {
    "forward1":      (1, (0, 1), 1, "一阶导·前向两点"),
    "backward1":     (1, (-1, 0), 1, "一阶导·后向两点"),
    "central2":      (1, (-1, 0, 1), 2, "一阶导·三点中心"),
    "forward2":      (1, (0, 1, 2), 2, "一阶导·前向三点（边界）"),
    "backward2":     (1, (-2, -1, 0), 2, "一阶导·后向三点（边界）"),
    "forward3":      (1, (0, 1, 2, 3), 3, "一阶导·前向四点（边界高精度）"),
    "backward3":     (1, (-3, -2, -1, 0), 3, "一阶导·后向四点（边界高精度）"),
    "central4":      (1, (-2, -1, 0, 1, 2), 4, "一阶导·五点中心"),
    "forward4":      (1, (0, 1, 2, 3, 4), 4, "一阶导·前向五点（边界高精度）"),
    "backward4":     (1, (-4, -3, -2, -1, 0), 4, "一阶导·后向五点（边界高精度）"),
    "central6":      (1, (-3, -2, -1, 0, 1, 2, 3), 6, "一阶导·七点中心"),
    "central2_d2":   (2, (-1, 0, 1), 2, "二阶导·三点中心"),
    "forward2_d2":   (2, (0, 1, 2, 3), 2, "二阶导·前向四点（边界）"),
    "backward2_d2":  (2, (-3, -2, -1, 0), 2, "二阶导·后向四点（边界）"),
    "forward3_d2":   (2, (0, 1, 2, 3, 4), 3, "二阶导·前向五点（边界高精度）"),
    "backward3_d2":  (2, (-4, -3, -2, -1, 0), 3, "二阶导·后向五点（边界高精度）"),
    "central4_d2":   (2, (-2, -1, 0, 1, 2), 4, "二阶导·五点中心"),
    "central6_d2":   (2, (-3, -2, -1, 0, 1, 2, 3), 6, "二阶导·七点中心"),
}


def derivative(f, x0, h, scheme="central2"):
    """用命名格式在 x0 处近似导数。

    参数:
        f:      可调用函数
        x0:     求导点
        h:      步长（>0）
        scheme: SCHEMES 中的格式名

    返回:
        (导数近似值, 函数求值次数)
    """
    if h <= 0:
        raise ValueError("h 必须为正")
    m, offsets, _, _ = SCHEMES[scheme]
    nodes = [x0 + k * h for k in offsets]
    w = fornberg_weights(nodes, x0, m)
    acc = 0.0
    for wi, xi in zip(w, nodes):
        acc += wi * f(xi)
    return acc, len(nodes)


def differentiate_grid(xs, ys, m=1, npoints=None):
    """在（可非等距）采样网格上求各点的 m 阶导数。

    对每个网格点，取以它为中心（边界处自动偏向单侧）的 npoints 个
    最近节点，用 Fornberg 权重构造该点的差分公式。

    参数:
        xs:      单调递增的采样点
        ys:      对应函数值
        m:       导数阶数
        npoints: 每个公式使用的节点数（默认 m+2，至少 m+1）

    返回:
        与 xs 等长的导数列表
    """
    xs = [float(v) for v in xs]
    ys = [float(v) for v in ys]
    n = len(xs)
    if len(ys) != n:
        raise ValueError("xs 与 ys 长度不一致")
    if any(xs[i + 1] <= xs[i] for i in range(n - 1)):
        raise ValueError("xs 必须严格递增")
    if npoints is None:
        npoints = m + 2
    npoints = max(npoints, m + 1)
    if npoints > n:
        raise ValueError(f"节点数 {n} 不足以构造 {npoints} 点公式")

    out = []
    for i in range(n):
        # 选以 i 为中心的 npoints 个节点，边界处截断
        lo = i - npoints // 2
        lo = max(0, min(lo, n - npoints))
        idx = list(range(lo, lo + npoints))
        nodes = [xs[j] for j in idx]
        w = fornberg_weights(nodes, xs[i], m)
        out.append(sum(wj * ys[j] for wj, j in zip(w, idx)))
    return out
