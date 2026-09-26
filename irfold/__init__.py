"""irfold: 中间表示的常量折叠与死代码检测库（仅标准库）。"""
from .interp import run, signature
from .ir import Program, parse
from .optimize import optimize

__all__ = ["parse", "run", "signature", "optimize", "Program"]
