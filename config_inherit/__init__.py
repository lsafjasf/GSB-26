"""多环境配置继承系统（仅标准库）。

层级与覆盖顺序（后者覆盖前者）：
    base -> 环境继承链（父环境 -> 子环境）-> local
"""

from .loader import (
    ConfigError,
    CycleError,
    LayerError,
    MissingRequiredError,
    TypeConflictError,
    load_config,
)

__all__ = [
    "ConfigError",
    "CycleError",
    "LayerError",
    "MissingRequiredError",
    "TypeConflictError",
    "load_config",
]
