"""区域化排序库（仅标准库）。

排序规则完全由 JSON 配置声明：
  - mappings: 字符 -> [主级, 次级, 三级] 权重（多级比较）
  - levels:   参与比较的级别名称（按优先级排列）
  - numeric_collation: 数字串是否按数值比较
  - fallback: 未覆盖字符的回退位置（"end" 排末尾 / "start" 排开头）

排序键按级别组织：先比较整个字符串的主级序列，再比较次级序列，
最后比较三级序列（与 UCA 的多级比较模型一致）。
"""

import json
import re

_DIGIT_RUN = re.compile(r"(\d+)")

# 元素类别标记，保证不同类别之间可比较且顺序确定：
#   0 = 数字串（按数值）  1 = 规则表覆盖的字符  2/-1 = 未覆盖字符（回退）
_KIND_NUM = 0
_KIND_MAPPED = 1
_KIND_UNKNOWN_END = 2
_KIND_UNKNOWN_START = -1


def _is_decimal_digit(ch):
    """是否参与数值比较的数字字符。

    必须与切分用的 _DIGIT_RUN（正则 \\d）保持同一口径：正则 \\d 只匹配
    Unicode 十进制数字（Nd 类，str.isdecimal），不含上标 ²³、下标 ₁ 等
    Nl/No 类“数字字符”（str.isdigit 会把后者也算进去）。
    """
    return ch.isdecimal()


class Collator:
    """由配置驱动的区域化比较器。"""

    def __init__(self, config):
        self.levels = list(config.get("levels", ["primary", "secondary", "tertiary"]))
        if not self.levels:
            raise ValueError("levels 不能为空")
        self.numeric = bool(config.get("numeric_collation", False))
        self.fallback = config.get("fallback", "end")
        if self.fallback not in ("end", "start"):
            raise ValueError("fallback 必须是 'end' 或 'start'")
        self.mappings = dict(config.get("mappings", {}))
        n = len(self.levels)
        for ch, weights in self.mappings.items():
            if len(ch) != 1:
                raise ValueError("mappings 的键必须是单个字符: %r" % ch)
            if len(weights) != n:
                raise ValueError("字符 %r 的权重数(%d)与级别数(%d)不一致"
                                 % (ch, len(weights), n))

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    # ---- 内部 ----

    def _elements(self, s):
        """把字符串切分为元素序列：("num", int) 或 ("char", str)。"""
        if not self.numeric:
            return [("char", c) for c in s]
        elements = []
        for part in _DIGIT_RUN.split(s):
            if not part:
                continue
            # 口径与 _is_decimal_digit 一致；防御性兜底：若某串看似数字
            # 却无法转成整数，则按普通字符逐字比较，而不是让整批排序中断。
            if part and all(_is_decimal_digit(ch) for ch in part):
                try:
                    value = int(part)
                except ValueError:
                    elements.extend(("char", ch) for ch in part)
                else:
                    elements.append(("num", value))
            else:
                elements.extend(("char", c) for c in part)
        return elements

    def _primary_weight(self, kind, value):
        if kind == "num":
            return (_KIND_NUM, value)
        weights = self.mappings.get(value)
        if weights is not None:
            return (_KIND_MAPPED, weights[0])
        mark = _KIND_UNKNOWN_END if self.fallback == "end" else _KIND_UNKNOWN_START
        return (mark, ord(value))

    def _other_weight(self, kind, value, level_index):
        if kind == "num":
            return 0
        weights = self.mappings.get(value)
        if weights is None:
            return 0
        return weights[level_index]

    def sort_key(self, s):
        """返回可比较的排序键（元组的元组），相同键 => 各级别完全相等。"""
        elements = self._elements(s)
        key = []
        for level_index in range(len(self.levels)):
            if level_index == 0:
                seq = tuple(self._primary_weight(k, v) for k, v in elements)
            else:
                seq = tuple(self._other_weight(k, v, level_index) for k, v in elements)
            key.append(seq)
        return tuple(key)

    def compare(self, a, b):
        """三路比较器：a<b 返回 -1，a==b 返回 0，a>b 返回 1。"""
        ka, kb = self.sort_key(a), self.sort_key(b)
        return (ka > kb) - (ka < kb)

    def sort(self, items, key=None):
        """稳定排序。key 为从记录中取排序字段的函数，缺省排序项本身。

        Python 的 sorted 是稳定排序：排序键相同的项保持原始相对顺序。
        """
        if key is None:
            return sorted(items, key=self.sort_key)
        return sorted(items, key=lambda item: self.sort_key(key(item)))

    def is_covered(self, ch):
        if self.numeric and _is_decimal_digit(ch):
            return True
        return ch in self.mappings

    def uncovered_chars(self, strings):
        """返回未覆盖字符 -> 出现次数 的字典（按码位排序）。"""
        counts = {}
        for s in strings:
            for ch in s:
                if not self.is_covered(ch):
                    counts[ch] = counts.get(ch, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: ord(kv[0])))
