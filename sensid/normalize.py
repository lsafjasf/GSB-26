"""扫描前规范化：剔除零宽与格式控制字符，并维护回原文的偏移映射。

剔除范围是 Unicode 类别 Cf（Format）字符，包括：
  - 零宽空格 U+200B、零宽非连接符/连接符 U+200C/U+200D
  - 左右向标记 U+200E/U+200F、双向格式控制 U+202A-U+202E、U+2066-U+2069
  - Word Joiner U+2060、不可见功能符 U+2061-U+2064
  - 软连字符 U+00AD、字节序标记/零宽不换行空格 U+FEFF 等

这些字符肉眼不可见，却会把同一号码在字面上拆开，导致按字面匹配的
正则漏报。扫描前先剔除它们，同时记录"规范化下标 -> 原文下标"的映射，
命中位置据此换算回原文偏移，保证报告里的位置仍然指向原文。
"""
from __future__ import annotations

import unicodedata


def is_ignorable(ch: str) -> bool:
    """是否为零宽/格式控制字符（Unicode 类别 Cf）。"""
    return unicodedata.category(ch) == "Cf"


def normalize_text(text: str) -> tuple[str, list[int]]:
    """剔除零宽与格式控制字符。

    返回 (规范化文本, index_map)，其中 index_map[i] 是规范化文本第 i 个
    字符在原文中的下标。
    """
    chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if is_ignorable(ch):
            continue
        chars.append(ch)
        index_map.append(i)
    return "".join(chars), index_map


def span_to_original(index_map: list[int], start: int, end: int) -> tuple[int, int]:
    """把规范化文本中的 [start, end) 跨度映射回原文 [ostart, oend)。"""
    return index_map[start], index_map[end - 1] + 1
