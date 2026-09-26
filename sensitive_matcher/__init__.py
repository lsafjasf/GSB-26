"""敏感词多模式匹配引擎（仅标准库）。"""

from .ac import AhoCorasick
from .matcher import Match, SensitiveMatcher
from .normalize import normalize, span_to_original

__all__ = ["AhoCorasick", "SensitiveMatcher", "Match", "normalize",
           "span_to_original"]
__version__ = "0.1.0"
