"""demo_robustness.py -- 稳健性对比实验：1% / 5% / 20% 离群点下的参数误差。

运行: python3 demo_robustness.py
"""

import math
import random

from robust_fit import fit_polynomial

TRUE_INTERCEPT, TRUE_SLOPE = 2.0, 3.0
N_SAMPLES = 200
NOISE_SIGMA = 0.5
TRIALS = 100


def make_dataset(frac_outliers, seed):
    rng = random.Random(seed)
    x = [10.0 * i / (N_SAMPLES - 1) for i in range(N_SAMPLES)]
    y = [TRUE_INTERCEPT + TRUE_SLOPE * xi + rng.gauss(0.0, NOISE_SIGMA)
         for xi in x]
    k = int(round(N_SAMPLES * frac_outliers))
    idx = rng.sample(range(N_SAMPLES), k)
    for i in idx:
        # 离群点：随机方向、10~20 倍噪声标准差的偏移
        y[i] += rng.choice([-1.0, 1.0]) * rng.uniform(10.0, 20.0) * NOISE_SIGMA
    return x, y


def param_error(params):
    return math.hypot(params[0] - TRUE_INTERCEPT, params[1] - TRUE_SLOPE)


def run_experiment():
    print(f"模型: y = {TRUE_INTERCEPT} + {TRUE_SLOPE}*x, "
          f"n={N_SAMPLES}, 高斯噪声 sigma={NOISE_SIGMA}, "
          f"离群点偏移 10~20*sigma, 每组 {TRIALS} 次重复取平均\n")
    header = (f"{'离群点比例':>10} | {'OLS 平均参数误差':>16} | "
              f"{'OLS 最大误差':>12} | {'IRLS 平均参数误差':>17} | "
              f"{'IRLS 最大误差':>13} | {'误差比 OLS/IRLS':>15}")
    print(header)
    print("-" * len(header))
    rows = []
    for frac in (0.01, 0.05, 0.20):
        errs = {"ols": [], "irls": []}
        for t in range(TRIALS):
            x, y = make_dataset(frac, seed=1000 + t)
            for method in errs:
                res = fit_polynomial(x, y, 1, method=method)
                errs[method].append(param_error(res.params))
        ols_mean = sum(errs["ols"]) / TRIALS
        irls_mean = sum(errs["irls"]) / TRIALS
        row = (frac, ols_mean, max(errs["ols"]),
               irls_mean, max(errs["irls"]), ols_mean / irls_mean)
        rows.append(row)
        print(f"{frac:>10.0%} | {row[1]:>16.4f} | {row[2]:>12.4f} | "
              f"{row[3]:>17.4f} | {row[4]:>13.4f} | {row[5]:>15.1f}")
    return rows


def demo_single_fit():
    print("\n--- 单次拟合示例 (5% 离群点) ---")
    x, y = make_dataset(0.05, seed=42)
    res = fit_polynomial(x, y, 1, method="irls")
    print(res.summary())
    print("离群点索引:", [i for i, f in enumerate(res.outliers) if f])
    print("前 10 个样本权重:", [round(w, 3) for w in res.weights[:10]])


if __name__ == "__main__":
    run_experiment()
    demo_single_fit()
