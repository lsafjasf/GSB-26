"""robustfit：仅依赖标准库的稳健曲线拟合库。"""

from .fit import (
    ols_fit, robust_fit, poly_design, mad_scale,
    FitResult, RankDeficientError, UnderdeterminedError,
)

__all__ = [
    "ols_fit", "robust_fit", "poly_design", "mad_scale",
    "FitResult", "RankDeficientError", "UnderdeterminedError",
]
