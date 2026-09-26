"""区域化排序库（仅标准库）。

排序规则完全由 JSON 配置声明：
  - letters: 拉丁字符 -> [主级字母, 次级变音等级]
  - cjk:     汉字 -> {"pinyin": 主级拼音, "tone": 次级声调, "alt": [其他读音]}
  - symbols: 符号 -> 显式排序位次
  - numeric: 数字串是否按数值比较
  - case_first / unknown_position: 大小写次序、未知字符回退位置

排序键为三级结构 (主级, 次级, 三级)，逐级比较：
  主级: 字母/拼音/数值/符号位次
  次级: 变音符号等级 / 声调
  三级: 大小写
"""

import json

TAG_SYMBOL = 0    # 符号（配置中显式给出位次）
TAG_NUMBER = 1    # 数字串（按数值）
TAG_ALPHA = 2     # 拉丁字母 / 汉字拼音
TAG_UNKNOWN_END = 9   # 未知字符回退到末尾
TAG_UNKNOWN_START = -1  # 未知字符回退到开头


class LocaleSorter:
    """按配置声明的规则生成排序键与比较器。"""

    def __init__(self, config):
        self.name = config.get("name", "unnamed")
        self.numeric = bool(config.get("numeric", True))
        self.case_first = config.get("case_first", "lower")
        self.unknown_position = config.get("unknown_position", "end")
        if self.unknown_position not in ("start", "end"):
            raise ValueError("unknown_position 必须是 'start' 或 'end'")
        self._unknown_tag = (
            TAG_UNKNOWN_END if self.unknown_position == "end" else TAG_UNKNOWN_START
        )

        # 拉丁字母表: char -> (primary, secondary)
        self._letters = {}
        for ch, entry in config.get("letters", {}).items():
            if len(ch) != 1:
                raise ValueError("letters 的键必须是单字符: %r" % ch)
            self._letters[ch] = (entry[0], int(entry[1]))

        # 符号表: char -> 位次
        self._symbols = {}
        for ch, rank in config.get("symbols", {}).items():
            if len(ch) != 1:
                raise ValueError("symbols 的键必须是单字符: %r" % ch)
            self._symbols[ch] = int(rank)

        # 汉字表: char -> (pinyin, tone)；多音字记录全部读音
        self._cjk = {}
        self.polyphones = {}
        for ch, entry in config.get("cjk", {}).items():
            if len(ch) != 1:
                raise ValueError("cjk 的键必须是单字符: %r" % ch)
            pinyin = entry["pinyin"]
            tone = int(entry.get("tone", 0))
            self._cjk[ch] = (pinyin, tone)
            alt = list(entry.get("alt") or [])
            if alt:
                self.polyphones[ch] = ["%s%d" % (pinyin, tone)] + alt

        # 未覆盖字符统计: char -> 出现次数
        self._uncovered = {}

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ------------------------------------------------------------------ key

    def _case_rank(self, ch):
        if ch.islower():
            return 0 if self.case_first == "lower" else 1
        if ch.isupper():
            return 1 if self.case_first == "lower" else 0
        return 0

    @staticmethod
    def _is_ascii_digit(ch):
        return "0" <= ch <= "9"

    def key(self, text):
        """返回三级排序键 (primaries, secondaries, tertiaries)，可互相比较。"""
        primaries = []
        secondaries = []
        tertiaries = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if self.numeric and self._is_ascii_digit(ch):
                j = i + 1
                while j < n and self._is_ascii_digit(text[j]):
                    j += 1
                primaries.append((TAG_NUMBER, int(text[i:j])))
                secondaries.append(0)
                tertiaries.append(0)
                i = j
                continue
            entry = self._letters.get(ch)
            if entry is not None:
                primaries.append((TAG_ALPHA, entry[0]))
                secondaries.append(entry[1])
                tertiaries.append(self._case_rank(ch))
            elif ch in self._cjk:
                pinyin, tone = self._cjk[ch]
                primaries.append((TAG_ALPHA, pinyin))
                secondaries.append(tone)
                tertiaries.append(0)
            elif ch in self._symbols:
                primaries.append((TAG_SYMBOL, self._symbols[ch]))
                secondaries.append(0)
                tertiaries.append(0)
            else:
                self._uncovered[ch] = self._uncovered.get(ch, 0) + 1
                primaries.append((self._unknown_tag, ord(ch)))
                secondaries.append(0)
                tertiaries.append(0)
            i += 1
        return (tuple(primaries), tuple(secondaries), tuple(tertiaries))

    # ------------------------------------------------------------- compare

    def compare(self, left, right):
        """全序比较器: 负数 / 0 / 正数。"""
        ka = self.key(left)
        kb = self.key(right)
        return (ka > kb) - (ka < kb)

    def sort(self, items):
        """稳定排序（list.sort 本身稳定），返回新列表。"""
        return sorted(items, key=self.key)

    # ------------------------------------------------------------- report

    def reset_uncovered(self):
        self._uncovered = {}

    def uncovered_report(self, items=None):
        """未覆盖字符报告: {字符: 次数}，按次数降序。

        若传入 items，则先扫描这些条目（不修改已有计数之外的副作用）。
        """
        if items is not None:
            for text in items:
                self.key(text)
        return dict(
            sorted(self._uncovered.items(), key=lambda kv: (-kv[1], kv[0]))
        )
