"""distfit：概率分布采样与拟合检验库（纯标准库）。

- 采样：Uniform / Exponential / Normal / Binomial，随机源可注入以便复现
- 检验：卡方（连续等概率分箱、离散自动合并）、KS、分位数对比
- 反推：fit() 从样本估计参数并做拟合优度检验，不匹配则 reject
"""
from .distributions import (Sampler, StdRandomSource, Uniform, Exponential,
                            Normal, Binomial)
from .gof import (goodness_of_fit, chi_square_continuous, chi_square_discrete,
                  ks_test, quantile_comparison, TestResult)
from .fit import fit, fit_best, FitResult

__all__ = [
    "Sampler", "StdRandomSource", "Uniform", "Exponential", "Normal", "Binomial",
    "goodness_of_fit", "chi_square_continuous", "chi_square_discrete",
    "ks_test", "quantile_comparison", "TestResult",
    "fit", "fit_best", "FitResult",
]
