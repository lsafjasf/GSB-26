"""各差分格式的精度阶验证：h 缩小 10 倍，误差应缩小约 10^p 倍。

对 SCHEMES 中每种格式，在截断误差主导的步长区间取 h 与 h/10 两点，
计算误差比 ratio = E(h) / E(h/10) 与观测阶 p_obs = log10(ratio)，
并以比例关系断言 ratio > 0.7 * 10^p（与 tests/test_numdiff.py 同判据）。

输出: data/order_verification.csv
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from numdiff import SCHEMES, derivative

X0 = 0.7
F = math.sin
EXACT = {1: math.cos(X0), 2: -math.sin(X0)}
_EPS = 2.220446049250313e-16


def truncation_dominated_h(p, m):
    """截断主导的基准步长：h 需远大于 eps^(1/(p+m))（截断=舍入的临界点）。"""
    return min(0.3, max(1e-2, 30.0 * _EPS ** (1.0 / (p + m))))


def main():
    rows = ["scheme,deriv_order,npoints,p_theory,h,err,err_h_over_10,"
            "ratio,p_observed,ok"]
    for name, (m, offsets, p, desc) in SCHEMES.items():
        h1 = truncation_dominated_h(p, m)
        h2 = h1 / 10.0
        e1 = abs(derivative(F, X0, h1, name)[0] - EXACT[m])
        e2 = abs(derivative(F, X0, h2, name)[0] - EXACT[m])
        ratio = e1 / max(e2, 1e-300)
        p_obs = math.log10(max(ratio, 1e-300))
        ok = ratio > 0.7 * 10.0 ** p
        rows.append(f"{name},{m},{len(offsets)},{p},{h1:.1e},{e1:.3e},"
                    f"{e2:.3e},{ratio:.4e},{p_obs:.2f},{ok}")
        assert ok, f"{name}: 阶验证失败 ratio={ratio:.3f}, 期望~1e{p}"

    out = os.path.join(os.path.dirname(__file__), "..", "data",
                       "order_verification.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("\n".join(rows) + "\n")
    print("\n".join(rows))
    print(f"\n全部 {len(rows) - 1} 种格式通过 10 倍步长比例断言，已写入 {out}")


if __name__ == "__main__":
    main()
