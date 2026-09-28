# distfit：概率分布采样与拟合检验库

纯 Python 3 标准库实现，无第三方依赖。

## 功能

- **采样**（随机源可注入，种子固定则结果可复现）：
  `Uniform(a,b)`、`Exponential(lam)`、`Normal(mu,sigma)`（Box-Muller）、
  `LogNormal(mu,sigma)`、`Binomial(n,p)`、`Poisson(lam)`
  （lam<10 用 Knuth 连乘，lam≥10 用 Hörmann PTRS 变换拒绝法，O(1)）
- **分布层检验**（不依赖均值/方差）：
  - 卡方检验：连续分布等概率分箱（每箱期望 ≥5）；离散分布相邻类别自动合并
  - KS 检验：小样本（n<30）自动启用
  - 分位数对比检验：`quantile_test` —— 样本分位数与理论分位数的联合渐近
    卡方检验（使用样本分位数的渐近协方差矩阵，Q = zᵀΣ⁻¹z ~ χ²(k)）
  - 分位数对照表：`quantile_comparison`
  - 输出统计量、临界值、p 值、自由度与 accept/reject 结论
- **参数反推**：`fit(samples, family)` 用 MLE/矩估计估计参数并立即做拟合优度检验
  （自由度按估计参数数扣减）；`fit_best(samples)` 尝试全部族并按 p 值排序。
  不匹配的样本（如指数样本拟合正态）会被 reject 并给出统计量依据。

## 参数校验与边界行为

- `LogNormal(mu, sigma)`：`mu` 必须有限，`sigma > 0`（sigma=0 的零方差退化
  拒绝构造，抛 `ValueError`）；sigma 极端小/大都合法，样本恒为正有限值。
- `Poisson(lam)`：`lam` 必须有限且 ≥ 0；`lam = 0` 是零方差退化分布，
  采样恒为 0（pmf(0)=1，support={0}）；大 lam（如 1e6）由 PTRS 精确采样，
  均值/方差仍等于 lam。
- 离散分布（Binomial/Poisson）通过 `support()` 给出有效支撑，
  卡方检验在该支撑上自动合并低期望类别。
- `quantile_test` 仅适用于连续分布（离散分布分位数存在大量并列值，
  传入离散分布抛 `ValueError`，请用卡方检验）。

## 运行

```bash
python3 selftest.py -v     # 36 项自测（种子复现/零样本/非法参数/边界/检验与反推）
python3 report.py          # 生成检验报告（样例见 report_sample.txt）
python3 benchmark.py       # 各分布采样 1,000,000 次耗时
```

## 用法示例

```python
from distfit import Sampler, StdRandomSource, Normal, goodness_of_fit, fit

s = Sampler(StdRandomSource(42))      # 注入带种子的随机源 -> 可复现
samples = s.normal(5000, mu=1, sigma=2)
print(goodness_of_fit(samples, Normal(1, 2)))   # 统计量/临界值/p值/结论
print(fit(samples, "normal"))                    # 反推 mu, sigma 并检验
```

自定义随机源：任何实现 `random() -> [0,1)` 的对象都可传给 `Sampler(source=...)`。

## 小样本与尾部的适用范围

- n=0 或 n=1：抛 `ValueError`，统计上无法检验。
- 2 ≤ n < 30：卡方分箱近似失效，自动改用 KS 检验；n 为个位数时 KS 临界值很大，
  只有极端不匹配才能拒绝（功效低是小样本的固有性质）。
- 参数由样本估计时，KS 的 p 值偏保守（Lilliefors 情形），报告会标注；
  卡方则通过扣减自由度近似修正。
- 尾部极端值：等概率分箱保证尾部箱期望频数与中部相同，尾部偏差不会被稀释；
  但极尾（p>0.999）在 n≈5000 时每箱仅约 5 个样本，涨落大，需更大样本分辨。

## 性能（Python 3.12, WSL2 x86-64, 每次采样 1,000,000 个）

| 分布 | 总耗时 | 单样本 |
|---|---|---|
| uniform(0,1) | 0.122 s | 122 ns |
| exponential(1) | 0.125 s | 125 ns |
| normal(0,1) | 0.152 s | 152 ns |
| lognormal(0,1) | 0.158 s | 158 ns |
| binomial(10,0.3) | 0.538 s | 538 ns |
| binomial(100,0.5) | 4.503 s | 4503 ns |
| poisson(4) | 0.268 s | 268 ns |
| poisson(1000) | 0.341 s | 341 ns |

二项采样为伯努利求和，复杂度 O(n)；大 n 场景可换几何等待时间或 BTPE 算法优化。
泊松采样 lam≥10 时为 O(1) 的 PTRS 变换拒绝法，lam=1000 与 lam=4 耗时接近。

## 文件

- `distfit/special.py` — 不完全伽马函数、卡方/正态 CDF 与分位数、KS p 值
- `distfit/distributions.py` — 分布类与 `Sampler`（随机源注入），含 PTRS 泊松采样
- `distfit/gof.py` — 卡方/KS/分位数对比检验
- `distfit/fit.py` — 参数反推与拟合优度
- `selftest.py` / `report.py` / `benchmark.py` / `report_sample.txt`
