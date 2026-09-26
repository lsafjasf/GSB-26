"""numdiff: 数值微分库（仅标准库）。

功能：
- 一阶/二阶导数的多种差分格式（前向/后向/中心/五点等）
- 任意（含非等距）网格上的 Fornberg 有限差分权重
- 自适应步长选择 + 误差估计（截断误差 vs 舍入/噪声误差平衡）
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

__all__ = [
    "fornberg_weights",
    "derivative",
    "differentiate_grid",
    "SCHEMES",
    "DiffResult",
    "adaptive_derivative",
    "optimal_step_theory",
]
