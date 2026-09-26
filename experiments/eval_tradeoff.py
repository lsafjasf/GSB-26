"""函数求值次数 vs 实际误差的权衡。

比较四种策略在三个不同尺度函数上的表现：
1. 固定步长中心差分（h 扫描）——便宜但必须预知合适的 h
2. 高阶固定模板 central4（5 次求值）
3. Richardson 外推（L 级，2L+1 次求值）
4. 自适应步长（本库）——无需调参，自动接近最优

输出: data/eval_tradeoff.csv
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from numdiff import adaptive_derivative, derivative

CASES = [
    ("sin@1", math.sin, math.cos, 1.0),
    ("sin@1e4", math.sin, math.cos, 1e4),
    ("exp@0.5", math.exp, math.exp, 0.5),
    # 快振荡：固定策略的预设步长完全失效，自适应仍可靠
    ("sin(1e3x)@0.7", lambda x: math.sin(1e3 * x),
     lambda x: 1e3 * math.cos(1e3 * x), 0.7),
]


def richardson(f, x0, h0, levels):
    """对中心差分做 L 级 Richardson 外推，返回 (值, 求值次数)。"""
    n = 0
    table = []
    for i in range(levels):
        v, k = derivative(f, x0, h0 / (2 ** i), "central2")
        n += k
        table.append([v])
    for j in range(1, levels):
        for i in range(levels - j):
            f4 = 4.0 ** j
            table[i].append(
                table[i + 1][j - 1] + (table[i + 1][j - 1] - table[i][j - 1]) / (f4 - 1))
    return table[0][-1], n


def main():
    rows = ["case,strategy,param,n_evals,actual_err"]
    for name, f, df, x0 in CASES:
        exact = df(x0)
        # 1. 固定步长中心差分，h 扫描（每个点 3 次求值）
        for e in range(1, 9):
            h = 10.0 ** (-e)
            v, n = derivative(f, x0, h, "central2")
            rows.append(f"{name},fixed_central2,h={h:.0e},{n},{abs(v - exact):.3e}")
        # 2. 高阶模板（5 次求值），取一个“合理”步长 h=0.01
        v, n = derivative(f, x0, 0.01, "central4")
        rows.append(f"{name},central4,h=1e-2,{n},{abs(v - exact):.3e}")
        # 3. Richardson 外推，L = 1..5 级
        for L in range(1, 6):
            v, n = richardson(f, x0, 0.05, L)
            rows.append(f"{name},richardson,L={L},{n},{abs(v - exact):.3e}")
        # 4. 自适应
        r = adaptive_derivative(f, x0, order=1)
        rows.append(f"{name},adaptive,auto,{r.n_evals},{abs(r.value - exact):.3e}")

    os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data"), exist_ok=True)
    out = os.path.join(os.path.dirname(__file__), "..", "data", "eval_tradeoff.csv")
    with open(out, "w") as fh:
        fh.write("\n".join(rows) + "\n")
    print("\n".join(rows))
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
