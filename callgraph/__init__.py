"""callgraph: 基于 Python 标准库 ast 的调用图构建与环检测库。

- 从源码提取函数定义与调用关系（无法静态确定的调用标注为不确定，绝不丢弃）
- Johnson 算法枚举基本环，区分自递归 / 相互递归
- 支持增量更新（只重扫受影响子图）
"""

import sys as _sys

if _sys.getrecursionlimit() < 200000:
    _sys.setrecursionlimit(200000)

from .graph import CallGraph, EdgeInfo
from .parser import parse_module, parse_function_source, ModuleModel, FunctionDef, CallSite
from .cycles import Cycle, find_cycles, verify_cycle
from .incremental import IncrementalEngine, UpdateReport
from .report import format_report

__all__ = [
    "CallGraph",
    "EdgeInfo",
    "parse_module",
    "parse_function_source",
    "ModuleModel",
    "FunctionDef",
    "CallSite",
    "Cycle",
    "find_cycles",
    "verify_cycle",
    "IncrementalEngine",
    "UpdateReport",
    "format_report",
]
