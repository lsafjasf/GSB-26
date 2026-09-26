"""有缺陷的文本分块/截断实现（保留用于回归对比）。

缺陷：直接按码位（code point）下标切片，完全无视字素簇边界：
1. 变音符号（组合字符）会与基字符被切到两个片段；
2. 表情符号序列（修饰符、ZWJ 序列、国旗）被拆成孤立码位；
3. 零宽连接符（ZWJ）可能残留在片段末尾；
4. 片段被逐段 NFC 规范化（分页/预览管道的常见步骤）后，
   重新拼接无法还原原文（组合字符失去基字符）。
"""


def chunk_text(text, size):
    """按码位数量切分，size 为每片最大码位数。"""
    if size <= 0:
        raise ValueError("size must be positive")
    return [text[i:i + size] for i in range(0, len(text), size)]


def truncate(text, limit):
    """按码位数量截断，limit 为最大码位数。"""
    if limit <= 0:
        return ""
    return text[:limit]


def truncate_bytes(text, budget):
    """按 UTF-8 字节预算截断（预览接口的常见写法）。

    缺陷：字节切口可能落在多字节序列中间，decode 时产生 U+FFFD，
    截断结果不再是原文的前缀，与剩余部分重新拼接无法还原原文。
    """
    if budget <= 0:
        return ""
    return text.encode("utf-8")[:budget].decode("utf-8", errors="replace")
