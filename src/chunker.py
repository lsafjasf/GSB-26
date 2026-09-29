"""修复后的文本分块/截断实现（仅标准库）。

核心思路：先按字素簇（grapheme cluster）切分，再在簇边界处分块/截断，
保证任何片段都不会以不完整的字素结尾，且各片段顺序拼接逐字符等于原文。

字素边界判定依据（UAX #29 的实用子集，基于 unicodedata）：
- Extend：组合记号（unicodedata.combining != 0，或类别 Mn/Mc/Me）、
  表情肤色修饰符 U+1F3FB..U+1F3FF、变体选择符 FE00..FE0F / E0100..E01EF
  不与其前的字符断开（GB9）。
- ZWJ (U+200D)：其前、其后均不断开（GB9/GB11 的保守合并），
  因此片段末尾永远不会残留孤立 ZWJ，家庭/职业类表情序列不会被拆散。
- Regional Indicator (U+1F1E6..U+1F1FF)：按两个一组配对（GB12/GB13），
  国旗不会被拆成单个地区指示符。
- 其余位置一律断开。文本以不完整序列开头（如首个字符就是组合记号）
  时，该字符自成一个退化簇；以不完整序列结尾（如末尾是 ZWJ）时，
  该字符并入前一个簇，边界规则天然覆盖这两种输入。

两种截断策略（由调用方通过 strategy 选择）：
- "codepoints"：按字素簇数量限制（与旧按码位限制并存，但绝不拆簇）。
  适合按“字符数”做配额的场景，与终端/字体渲染宽度无关。
- "width"：按显示宽度限制（East Asian Width：W/F 计 2，组合记号、
  肤色修饰符、变体选择符、控制符、格式符计 0，其余计 1；
  含 ZWJ 的簇整体按一个显示单元计 2）。适合终端列宽、定宽预览
  等场景。同一个字素簇永不拆分：truncate 的实际宽度可能略小于
  limit 但绝不超出；chunk_text 中若单个簇自身宽度超过 size，
  该簇独占一个片段（唯一允许超出的例外，见 chunk_text 文档）。
- "bytes"：按 UTF-8 字节预算限制。适合存储/传输按字节计费的场景；
  截断点必为字素边界，截断结果与原文剩余部分可直接拼接还原。
"""

import unicodedata

ZWJ = "\u200d"

_STRATEGIES = ("codepoints", "width", "bytes")


def _is_extend(ch):
    if unicodedata.combining(ch):
        return True
    if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return True
    cp = ord(ch)
    if 0x1F3FB <= cp <= 0x1F3FF:  # emoji skin-tone modifiers
        return True
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:  # variation selectors
        return True
    return False


def _is_regional_indicator(ch):
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def iter_graphemes(text):
    """生成器：按字素簇产出 text 的片段，拼接结果恒等于 text。"""
    cluster = ""
    ri_run = 0  # 当前簇尾部连续 Regional Indicator 的个数
    for ch in text:
        if not cluster:
            cluster = ch
            ri_run = 1 if _is_regional_indicator(ch) else 0
            continue
        prev = cluster[-1]
        if _is_extend(ch) or ch == ZWJ or prev == ZWJ:
            cluster += ch
        elif _is_regional_indicator(ch) and _is_regional_indicator(prev) and ri_run % 2 == 1:
            cluster += ch
            ri_run += 1
            continue
        else:
            yield cluster
            cluster = ch
        ri_run = 1 if _is_regional_indicator(ch) else 0
    if cluster:
        yield cluster


def graphemes(text):
    return list(iter_graphemes(text))


def char_width(ch):
    """单个码位的显示宽度（wcwidth 的简化版）。

    Extend 类字符（组合记号、肤色修饰符、变体选择符）不单独占宽，
    计 0；否则修饰符会被重复计宽（如 👍🏽 被算成 4 而非 2）。
    """
    if _is_extend(ch):
        return 0
    if unicodedata.category(ch) in ("Cc", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def grapheme_width(cluster):
    """字素簇的显示宽度。ZWJ 序列整体渲染为一个字形，计 2。"""
    if ZWJ in cluster:
        return 2
    return sum(char_width(ch) for ch in cluster)


def _cluster_cost(cluster, strategy):
    if strategy == "codepoints":
        return 1
    if strategy == "width":
        return grapheme_width(cluster)
    return len(cluster.encode("utf-8"))


def _check_strategy(strategy):
    if strategy not in _STRATEGIES:
        raise ValueError("strategy must be one of %r" % (_STRATEGIES,))


def chunk_text(text, size, *, strategy="codepoints"):
    """把 text 切成若干片段，每片不超过 size 个单位（字素簇数或显示宽度）。

    保证："".join(chunk_text(text, size, strategy=...)) == text，
    且除原文自身结尾外，任何片段都不以不完整字素结尾。

    显式例外：字素簇不可拆分，因此当单个簇的成本（如显示宽度）本身
    超过 size 时，该簇独占一个片段，此片段是唯一允许超出 size 的情形。
    （truncate 不同：超预算的簇会被整体丢弃，结果绝不超出 limit。）
    """
    _check_strategy(strategy)
    if size <= 0:
        raise ValueError("size must be positive")
    chunks = []
    current = ""
    used = 0
    for cluster in iter_graphemes(text):
        cost = _cluster_cost(cluster, strategy)
        if current and used + cost > size:
            chunks.append(current)
            current = ""
            used = 0
        current += cluster
        used += cost
    if current:
        chunks.append(current)
    return chunks


def truncate(text, limit, *, strategy="codepoints"):
    """把 text 截断到不超过 limit 个单位，截断点必为字素边界。"""
    _check_strategy(strategy)
    if limit <= 0:
        return ""
    out = ""
    used = 0
    for cluster in iter_graphemes(text):
        cost = _cluster_cost(cluster, strategy)
        if used + cost > limit:
            break
        out += cluster
        used += cost
    return out
