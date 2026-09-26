"""sensfind：敏感数据发现库（仅标准库）。

用法：
    from sensfind import Engine
    result = Engine(threshold=0.6).scan(text)
    for f in result.findings:
        print(f.type, f.value, f.confidence, f.reasons)
"""
from .engine import Engine, Finding, ScanResult, CONFLICT_RULE

__all__ = ["Engine", "Finding", "ScanResult", "CONFLICT_RULE"]
__version__ = "0.1.0"
