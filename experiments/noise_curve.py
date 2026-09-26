"""噪声水平 vs 数值微分误差曲线。

对 f=sin 在 x0=1 处叠加幅度为 delta 的均匀噪声，比较：
- 自适应微分的实际误差 / 报告估计
- 理论最优误差 E* ~ delta^(2/3)（一阶）、delta^(1/2)（二阶）
- 固定步长（h=1e-4，为无噪声优化的步长）在噪声下的表现

输出: data/noise_curve.csv
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from numdiff import adaptive_derivative, derivative, optimal_step_theory

X0 = 1.0
EXACT_D1 = math.cos(X0)
EXACT_D2 = -math.sin(X0)


def main():
    rng = random.Random(20260927)
    rows = ["noise,order,actual_err,error_est,theory_opt_err,fixed_h_err,n_evals"]
    for k in range(13):
        noise = 10.0 ** (-13 + k)  # 1e-13 .. 1e-1

        def noisy_sin(x, _n=noise):
            return math.sin(x) + _n * (2.0 * rng.random() - 1.0)

        for order, exact in ((1, EXACT_D1), (2, EXACT_D2)):
            r = adaptive_derivative(noisy_sin, X0, order=order, noise=noise)
            actual = abs(r.value - exact)
            # 理论最优误差（用解析高阶导数代入平衡公式）
            hd = abs(math.cos(X0)) if order == 1 else abs(math.sin(X0))
            _, e_star = optimal_step_theory(order, 1.0, hd, noise)
            # 固定步长 h=1e-4（无噪声时接近最优）在噪声下退化
            scheme = "central2" if order == 1 else "central2_d2"
            val, _ = derivative(noisy_sin, X0, 1e-4, scheme)
            fixed_err = abs(val - exact)
            rows.append(f"{noise:.1e},{order},{actual:.3e},{r.error_est:.3e},"
                        f"{e_star:.3e},{fixed_err:.3e},{r.n_evals}")

    os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data"), exist_ok=True)
    out = os.path.join(os.path.dirname(__file__), "..", "data", "noise_curve.csv")
    with open(out, "w") as fh:
        fh.write("\n".join(rows) + "\n")
    print("\n".join(rows))
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
