"""Fornberg 有限差分权重：在任意（含非等距）节点上求任意阶导数的权重。

参考: B. Fornberg, "Generation of Finite Difference Formulas on
Arbitrarily Spaced Grids", Math. Comp. 51 (1988)。

给定节点 x[0..n] 与求导点 x0，返回权重 w[i] 使得
    sum_i w[i] * f(x[i])  ~=  f^{(m)}(x0)
对次数 <= n 的多项式精确。
"""

from __future__ import annotations


def fornberg_weights(x, x0, m):
    """计算在点 x0 处 m 阶导数的有限差分权重。

    参数:
        x:  节点列表（长度 n+1，可非等距、可不包含 x0）
        x0: 求导点
        m:  导数阶数（0 <= m <= n）

    返回:
        权重列表 w，len(w) == len(x)
    """
    x = [float(v) for v in x]
    x0 = float(x0)
    n = len(x) - 1
    if not 0 <= m <= n:
        raise ValueError(f"需要 0 <= m <= n，得到 m={m}, n={n}")

    # c1..c5 为 Fornberg 递推中的临时量；c 为权重表 c[i][j]
    c = [[0.0] * (m + 1) for _ in range(n + 1)]
    c1, c4 = 1.0, x[0] - x0
    c[0][0] = 1.0
    for i in range(1, n + 1):
        mn = min(i, m)
        c2, c5 = 1.0, c4
        c4 = x[i] - x0
        for j in range(i):
            c3 = x[i] - x[j]
            c2 *= c3
            if j == i - 1:
                for k in range(mn, 0, -1):
                    c[i][k] = c1 * (k * c[i - 1][k - 1] - c5 * c[i - 1][k]) / c2
                c[i][0] = -c1 * c5 * c[i - 1][0] / c2
            for k in range(mn, 0, -1):
                c[j][k] = (c4 * c[j][k] - k * c[j][k - 1]) / c3
            c[j][0] = c4 * c[j][0] / c3
        c1 = c2
    # c[j][k] 是节点 j 在 k 阶导数上的权重；取最后一列
    return [c[j][m] for j in range(n + 1)]
