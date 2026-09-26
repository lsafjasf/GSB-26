"""稳健性对比实验：1% / 5% / 20% 离群点下 OLS 与 IRLS(Tukey) 的参数误差。

运行：python3 demo.py
"""

import math
import random

from robustfit import ols_fit, robust_fit, poly_design

TRUE_BETA = [1.5, 2.0, -0.3]   # y = 1.5 + 2x - 0.3 x^2
N_SAMPLES = 200
NOISE_STD = 0.5
N_SEEDS = 30


def make_dataset(rng, outlier_frac):
    xs = [rng.uniform(-5.0, 5.0) for _ in range(N_SAMPLES)]
    ys = []
    for x in xs:
        y = TRUE_BETA[0] + TRUE_BETA[1] * x + TRUE_BETA[2] * x * x
        ys.append(y + rng.gauss(0.0, NOISE_STD))
    n_out = max(1, round(N_SAMPLES * outlier_frac))
    for i in rng.sample(range(N_SAMPLES), n_out):
        # 离群点：大幅纵向偏移，方向随机
        ys[i] += rng.choice([-1.0, 1.0]) * rng.uniform(8.0, 15.0)
    return xs, ys


def rel_param_error(beta):
    num = math.sqrt(sum((b - t) ** 2 for b, t in zip(beta, TRUE_BETA)))
    den = math.sqrt(sum(t * t for t in TRUE_BETA))
    return num / den


def main():
    print("=" * 78)
    print("稳健性对比：二次多项式拟合，N=200，噪声 sigma=0.5，离群点偏移 ±[8,15]")
    print(f"每个配置重复 {N_SEEDS} 次取平均；误差 = ||beta_hat - beta|| / ||beta||（相对）")
    print("=" * 78)
    header = (f"{'离群比例':>8} | {'OLS 平均误差':>14} | {'OLS 最差':>10} | "
              f"{'IRLS 平均误差':>14} | {'IRLS 最差':>10} | {'误差比 OLS/IRLS':>14}")
    print(header)
    print("-" * len(header))
    for frac in (0.01, 0.05, 0.20):
        errs_ols, errs_irls = [], []
        for seed in range(N_SEEDS):
            rng = random.Random(1000 + seed)
            xs, ys = make_dataset(rng, frac)
            design = poly_design(xs, 2)
            errs_ols.append(rel_param_error(ols_fit(design, ys).beta))
            errs_irls.append(rel_param_error(robust_fit(design, ys).beta))
        mo, mi = sum(errs_ols) / N_SEEDS, sum(errs_irls) / N_SEEDS
        print(f"{frac:>8.0%} | {mo:>14.4e} | {max(errs_ols):>10.2e} | "
              f"{mi:>14.4e} | {max(errs_irls):>10.2e} | {mo / mi:>13.1f}x")

    print()
    print("=" * 78)
    print("单次拟合详细报告（5% 离群点，seed=42）")
    print("=" * 78)
    rng = random.Random(42)
    xs, ys = make_dataset(rng, 0.05)
    design = poly_design(xs, 2)
    print(f"真实参数: {TRUE_BETA}\n")
    for res in (ols_fit(design, ys), robust_fit(design, ys)):
        print(res.summary())
        flagged = [i for i, o in enumerate(res.outliers) if o]
        print(f"被标记离群的样本下标: {flagged}")
        print("-" * 78)


if __name__ == "__main__":
    main()
