"""接口契约测试框架（仅标准库）。"""
from .core import (
    Context,
    Outcome,
    CaseResult,
    Diff,
    Runner,
    run_case,
    behavior_diffs,
    judge,
    VERDICT_REPLACEABLE,
    VERDICT_CONDITIONAL,
    VERDICT_INCOMPATIBLE,
)

__all__ = [
    "Context",
    "Outcome",
    "CaseResult",
    "Diff",
    "Runner",
    "run_case",
    "behavior_diffs",
    "judge",
    "VERDICT_REPLACEABLE",
    "VERDICT_CONDITIONAL",
    "VERDICT_INCOMPATIBLE",
]
