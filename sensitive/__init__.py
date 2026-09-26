"""敏感词多模式匹配引擎（纯标准库）。

公开接口：

* :class:`SensitiveEngine` -- 主引擎（Aho-Corasick + 归一化 + 误伤控制）；
* :class:`Hit` -- 命中结果；
* :class:`Normalizer` / :class:`NormalizeConfig` / :class:`Normalized`
  -- 归一化与位置映射；
* :class:`AhoCorasick` -- 裸自动机；
* :func:`naive_scan` / :func:`naive_find_all` -- 朴素逐词扫描参考实现。
"""

from .ahocorasick import AhoCorasick
from .engine import Hit, SensitiveEngine, ascii_word_char
from .naive import naive_find_all, naive_scan
from .normalize import (
    DEFAULT_CONFIG,
    RAW_CONFIG,
    NormalizeConfig,
    Normalized,
    Normalizer,
)

__all__ = [
    "SensitiveEngine",
    "Hit",
    "Normalizer",
    "NormalizeConfig",
    "Normalized",
    "AhoCorasick",
    "ascii_word_char",
    "naive_scan",
    "naive_find_all",
    "DEFAULT_CONFIG",
    "RAW_CONFIG",
]
