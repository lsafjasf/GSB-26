"""numdiff: 数值微分库（仅标准库）。

功能：
- 一阶/二阶导数的多种差分格式（2~7 点，前向/后向/中心，可选）
- 任意（含非等距）网格上的 Fornberg 有限差分权重
- 自适应步长选择 + 误差估计（截断误差 vs 舍入/噪声误差平衡）
- 含噪数据的偏差—方差权衡曲线、最佳步长搜索与自适应停止
- 边界点单侧格式
"""

from .weights import fornberg_weights
from .schemes import (
    derivative,
    differentiate_grid,
    SCHEMES,
)
from .adaptive import (
    DiffResult,
    adaptive_derivative,
    optimal_step_theory,
)
from .noise import (
    adaptive_noisy_derivative,
    bias_variance_curve,
    error_model,
    scheme_error_coeffs,
    search_best_step,
)

__all__ = [
    "fornberg_weights",
    "derivative",
    "differentiate_grid",
    "SCHEMES",
    "DiffResult",
    "adaptive_derivative",
    "optimal_step_theory",
    "adaptive_noisy_derivative",
    "bias_variance_curve",
    "error_model",
    "scheme_error_coeffs",
    "search_best_step",
]
