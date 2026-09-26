"""数值线性代数内核：Householder QR 分解、三角求解、条件数估计。

设计原则：
- 绝不构造或求逆正规方程矩阵 A^T A（其条件数是 cond(A)^2，会放大误差）。
- 所有最小二乘问题都通过对设计矩阵 A 直接做 Householder QR 求解，
  cond(R) == cond(A)，数值稳定。
"""

import math

# 秩判定阈值：R 对角元小于 max_diag * RANK_TOL 视为秩亏
RANK_TOL = 1e-12


class RankDeficientError(Exception):
    """设计矩阵秩亏（如完全共线）时抛出。"""

    def __init__(self, column, message=None):
        self.column = column
        super().__init__(message or
            f"设计矩阵秩亏：第 {column} 列与其前列（近似）线性相关，"
            f"模型不可辨识。请删除冗余基函数或降低多项式阶数。")


def householder_qr(a):
    """对 m x n (m >= n) 矩阵做 Householder QR 分解。

    返回 (r, vs)：
      r  — 上三角因子（m x n，前 n 行为 R）
      vs — 每列的 Householder 向量（已归一化），用于隐式应用 Q^T，
           避免显式构造 Q（省内存且更稳定）。零列位置为 None。
    """
    m = len(a)
    n = len(a[0])
    r = [row[:] for row in a]
    vs = []
    for k in range(n):
        sigma = math.sqrt(sum(r[i][k] ** 2 for i in range(k, m)))
        if sigma == 0.0:
            vs.append(None)
            continue
        v = [0.0] * m
        for i in range(k, m):
            v[i] = r[i][k]
        # 选取符号避免相消误差
        v[k] += math.copysign(sigma, r[k][k])
        vnorm = math.sqrt(sum(x * x for x in v))
        v = [x / vnorm for x in v]
        vs.append(v)
        # 对剩余列应用反射 H = I - 2 v v^T
        for j in range(k, n):
            dot = sum(v[i] * r[i][j] for i in range(k, m))
            for i in range(k, m):
                r[i][j] -= 2.0 * v[i] * dot
    return r, vs


def apply_qt(vs, b):
    """对向量 b 隐式左乘 Q^T（原地修改并返回）。"""
    b = list(b)
    m = len(b)
    for k, v in enumerate(vs):
        if v is None:
            continue
        dot = sum(v[i] * b[i] for i in range(k, m))
        for i in range(k, m):
            b[i] -= 2.0 * v[i] * dot
    return b


def back_substitute(r, y, n):
    """解上三角系统 R x = y（R 取 r 的前 n 行）。

    若对角元低于秩阈值，抛出 RankDeficientError。
    """
    diag_max = max(abs(r[k][k]) for k in range(n)) if n else 0.0
    x = [0.0] * n
    for k in range(n - 1, -1, -1):
        if abs(r[k][k]) <= RANK_TOL * max(diag_max, 1e-300):
            raise RankDeficientError(k)
        s = y[k] - sum(r[k][j] * x[j] for j in range(k + 1, n))
        x[k] = s / r[k][k]
    return x


def qr_solve(a, b):
    """最小二乘解 min ||A x - b||，返回 (x, r, vs)。"""
    n = len(a[0])
    r, vs = householder_qr(a)
    check_rank(r, n)
    qt_b = apply_qt(vs, b)
    x = back_substitute(r, qt_b, n)
    return x, r, vs


def check_rank(r, n):
    """按列升序检查秩，第一个秩亏列抛出 RankDeficientError。"""
    diag_max = max(abs(r[k][k]) for k in range(n)) if n else 0.0
    for k in range(n):
        if abs(r[k][k]) <= RANK_TOL * max(diag_max, 1e-300):
            raise RankDeficientError(k)


def cond_estimate(r, n, iters=100):
    """估计 cond_2(A) = cond_2(R) = sigma_max / sigma_min。

    方法：对 R^T R 做幂迭代估计 sigma_max^2，
    对 (R^T R)^{-1} 做逆幂迭代（用三角回代）估计 sigma_min^2。
    这是标准的估计手段，误差通常在百分之几以内。
    """
    if n == 0:
        return 0.0
    diag_min = min(abs(r[k][k]) for k in range(n))
    diag_max = max(abs(r[k][k]) for k in range(n))
    if diag_min <= RANK_TOL * max(diag_max, 1e-300):
        return math.inf

    def matvec_rt_r(x):
        # y = R x，再 z = R^T y
        y = [sum(r[i][j] * x[j] for j in range(i, n)) for i in range(n)]
        z = [0.0] * n
        for i in range(n):
            for j in range(i + 1):
                z[j] += r[j][i] * y[i]
        return z

    def solve_rt_r(x):
        # 解 R^T R z = x：先前代解 R^T y = x，再回代解 R z = y
        y = [0.0] * n
        for k in range(n):
            y[k] = (x[k] - sum(r[j][k] * y[j] for j in range(k))) / r[k][k]
        return back_substitute(r, y, n)

    def norm(v):
        return math.sqrt(sum(t * t for t in v))

    # 幂迭代 -> sigma_max
    x = [1.0 / math.sqrt(n)] * n
    lam_max = 1.0
    for _ in range(iters):
        x = matvec_rt_r(x)
        lam_max = norm(x)
        if lam_max == 0.0:
            return math.inf
        x = [t / lam_max for t in x]
    # 逆幂迭代 -> sigma_min
    x = [1.0 / math.sqrt(n)] * n
    lam_min_inv = 1.0
    for _ in range(iters):
        x = solve_rt_r(x)
        lam_min_inv = norm(x)
        x = [t / lam_min_inv for t in x]
    sigma_max = math.sqrt(lam_max)
    sigma_min = 1.0 / math.sqrt(lam_min_inv)
    return sigma_max / sigma_min
