"""sensid：敏感数据发现库（仅标准库）。

用法：
    from sensid import Scanner
    result = Scanner(threshold=0.5).scan(text)
    for m in result.matches:
        print(m.type, m.start, m.end, m.score, m.reasons)
"""
from .engine import CONFLICT_RULE, Scanner, scan_text
from .types import Conflict, Match, Rejection, ScanResult, Scored

__all__ = [
    "Scanner", "scan_text", "CONFLICT_RULE",
    "Match", "Rejection", "Conflict", "Scored", "ScanResult",
]
__version__ = "0.1.0"
