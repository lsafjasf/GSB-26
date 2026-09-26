"""文本归一化 + 原文位置映射。

所有归一化都是可选项。normalize() 返回 (归一化文本, index_map)，
其中 index_map[i] 是归一化文本第 i 个字符在原文中的偏移。
归一化区间 [s, e) 映射回原文区间为 [index_map[s], index_map[e-1] + 1)。
"""

# 常见零宽 / 不可见字符
ZERO_WIDTH_CHARS = frozenset([
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u2060",  # WORD JOINER
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE / BOM
    "\u00ad",  # SOFT HYPHEN
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
    "\ufe00", "\ufe01", "\ufe02", "\ufe03",  # VARIATION SELECTOR-1..4（常用子集）
])


def fullwidth_to_halfwidth_char(ch):
    """全角 ASCII/空格转半角，其余字符原样返回。1:1 映射。"""
    code = ord(ch)
    if code == 0x3000:  # 全角空格
        return " "
    if 0xFF01 <= code <= 0xFF5E:
        return chr(code - 0xFEE0)
    return ch


def normalize(text, *, lowercase=False, fullwidth_to_halfwidth=False,
              strip_whitespace=False, strip_zero_width=False):
    """归一化文本并保留位置映射。

    返回 (normalized_text, index_map)：
      - normalized_text: 归一化后的字符串
      - index_map: list[int]，index_map[i] 为归一化第 i 字符对应的原文偏移

    说明：
      - lowercase 使用 str.casefold()（比 lower 更彻底，如 'ß' -> 'ss'）。
        一个原文字符可能展开为多个归一化字符，这些字符都映射回同一原文偏移。
      - strip_whitespace / strip_zero_width 会删除字符，被删字符不出现在
        归一化结果中，因此不会产生命中，位置映射天然保持正确。
    """
    chars = []
    index_map = []
    for i, ch in enumerate(text):
        if strip_zero_width and ch in ZERO_WIDTH_CHARS:
            continue
        if strip_whitespace and ch.isspace():
            continue
        if fullwidth_to_halfwidth:
            ch = fullwidth_to_halfwidth_char(ch)
        if lowercase:
            ch = ch.casefold()
        for c in ch:
            chars.append(c)
            index_map.append(i)
    return "".join(chars), index_map


def span_to_original(index_map, start, end):
    """把归一化区间 [start, end) 映射回原文区间 [orig_start, orig_end)。"""
    return index_map[start], index_map[end - 1] + 1
