# GSB-26 — probdist: 概率分布采样与拟合检验库

纯 Python 3 标准库实现，无第三方依赖。面向模拟与压测场景：采样器由可注入随机源驱动（可复现），正确性用分布层面的检验（卡方 / KS / 分位数对比）验证，而非只看均值方差。

## 结构

- `probdist/distributions.py` — 均匀、指数、正态（Box-Muller）、二项采样器 + CDF/分位数/PMF
- `probdist/special.py` — 不完全伽马函数、卡方 CDF/逆 CDF、正态逆 CDF（Acklam）、Kolmogorov 分布
- `probdist/gof.py` — 卡方检验、KS 检验、分位数对比，返回统计量 / 临界值 / p 值 / 结论
- `probdist/fit.py` — 从样本反推参数（MLE / 矩估计）并做拟合优度检验
- `selftest.py` — 自测套件（同时生成检验报告样例）
- `benchmark.py` — 百万次采样耗时
- `report_sample.txt` — 一次完整自测的输出样例

## 运行命令

```bash
python3 selftest.py    # 自测 + 检验报告（退出码非 0 表示失败）
python3 benchmark.py   # 1,000,000 次采样计时
```

## 用法示例

```python
import random
from probdist import distributions as dist, gof, fit

rng = random.Random(42)                      # 同种子 => 同样本序列
samples = dist.sample_exponential(rng, lam=1.5, n=20000)

# 分布层检验：统计量、临界值、p 值、结论
r = gof.chi_square_uniform_bins(samples,
        cdf=lambda x: dist.exponential_cdf(x, 1.5),
        ppf=lambda p: dist.exponential_ppf(p, 1.5))
print(r)   # [chi-square] statistic=... critical(0.05)=... p-value=... -> PASS/FAIL

# 参数反推 + 拟合优度
res = fit.fit_normal(samples)                # 拿指数样本硬拟合正态会被拒绝
print(res.params, res.gof.conclusion)
```

## 近似的适用范围与失效表现

- **卡方检验**：每箱期望频数需 ≥ ~5（Cochran 规则）；连续分布用等概率分箱，离散分布自动合并相邻取值。期望频数不足时结果中带 `WARNING`，结论仅供参考。拟合参数会扣减自由度（仍是近似，样本量大时可靠）。
- **小样本（个位数）**：任何分布检验都没有实际检验力（power），极易"放过"错误分布；库会明确给出 small-sample 警告而不是假装结论可靠。
- **KS 检验**：p 值用 Kolmogorov 渐近分布，n < ~35 时偏保守（该拒绝时拒绝得不够）；离散数据上同样保守。
- **分位数对比**：各分位数用二项计数接受带（Bonferroni 校正），专门盯尾部；统计量为越界分位数个数，零假设下近似服从 Binomial(分位数个数, α/分位数个数)，临界值与 p 值由该分布算出，判定口径与其他检验一致（统计量 > 临界值 ⟺ p ≤ α ⟺ 拒绝）；n < 50 时 1%/99% 尾部分位基本不可检。
- **正态逆 CDF**：Acklam 有理逼近，最大绝对误差 ~1.15e-9，对检验用途足够。

## 性能数据（Python 3.12.3, x86_64, n=1,000,000, 3 次取最优）

| 分布 | 耗时 | 速率 |
|---|---|---|
| uniform(0,1) | 0.045 s | ~22 M samples/s |
| exponential(1.5) | 0.070 s | ~14 M samples/s |
| normal(0,1) | 0.103 s | ~10 M samples/s |
| binomial(20,0.3) | 0.733 s | ~1.4 M samples/s |

二项采样是逐次伯努利试验（O(trials)），大 trials 时可用正态/泊松近似替换。

## 已覆盖的边界情形

- 同一种子 → 完全相同的样本序列（四种分布均验证）
- `n=0` 返回空列表；对空样本做检验/拟合抛 `ValueError`
- 非法参数（`low>=high`、`lam<=0`、`sigma<=0`、`p∉[0,1]`、`trials<0`、`n<0`）均抛 `ValueError`
- 明显不匹配的样本（指数数据拟正态等）被拒绝，并给出统计量/临界值/p 值作为依据
- 重尾污染（1% 极端值）被分位数检验捕获——均值/方差层面几乎不可见
