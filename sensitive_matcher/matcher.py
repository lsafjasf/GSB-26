"""敏感词匹配引擎：AC 自动机 + 归一化 + 词边界 + 白名单。"""

from dataclasses import dataclass

from .ac import AhoCorasick
from .normalize import normalize, span_to_original


def _is_word_char(ch):
    """词边界判定用的"词字符"：ASCII 字母、数字、下划线。

    刻意只认 ASCII：CJK 字符不算词字符，因此中文词（如 "赌博"）
    在中文语境中不会被边界规则误拦；而 "ass" 在 "class" 中仍会被
    正确拦截。若需对中文也启用严格边界，应自行扩展此判定。
    """
    return ch.isascii() and (ch.isalnum() or ch == "_")


@dataclass(frozen=True)
class Match:
    """一次命中。start/end 为**原文**偏移（end 开区间）。"""
    word: str       # 命中的词表原词
    start: int      # 原文起始偏移（含）
    end: int        # 原文结束偏移（不含）
    text: str       # 原文中实际命中的子串（便于核对归一化前后的差异）


class SensitiveMatcher:
    """用法：
        m = SensitiveMatcher(lowercase=True, boundary="both")
        m.add_all(["ass", "赌博"])
        m.build()
        for hit in m.finditer(text): ...

    参数：
      lowercase / fullwidth_to_halfwidth / strip_whitespace / strip_zero_width:
          归一化开关，作用于词表与待扫描文本（同一套规则）。
      boundary: "none" | "both" | "left" | "right"
          词边界判定在归一化后的文本上进行：命中区间的左/右相邻字符
          不能是词字符（字母/数字/下划线）。
      whitelist: 白名单词表。凡是被某个白名单命中区间**完整包含**的
          敏感词命中都会被抑制（如白名单 "旋转木马" 抑制 "木马"）。
    """

    def __init__(self, *, lowercase=False, fullwidth_to_halfwidth=False,
                 strip_whitespace=False, strip_zero_width=False,
                 boundary="none", whitelist=()):
        if boundary not in ("none", "both", "left", "right"):
            raise ValueError(f"bad boundary rule: {boundary!r}")
        self._norm_opts = dict(
            lowercase=lowercase,
            fullwidth_to_halfwidth=fullwidth_to_halfwidth,
            strip_whitespace=strip_whitespace,
            strip_zero_width=strip_zero_width,
        )
        self._boundary = boundary
        self._ac = AhoCorasick()
        self._wl_ac = AhoCorasick()
        self._has_whitelist = False
        for w in whitelist:
            self.add_whitelist(w)

    def _normalize_word(self, word):
        norm, _ = normalize(word, **self._norm_opts)
        return norm

    def add(self, word):
        norm = self._normalize_word(word)
        if not norm:
            raise ValueError(f"pattern normalizes to empty: {word!r}")
        self._ac.add(norm)

    def add_all(self, words):
        for w in words:
            self.add(w)

    def add_whitelist(self, word):
        norm = self._normalize_word(word)
        if not norm:
            raise ValueError(f"whitelist pattern normalizes to empty: {word!r}")
        self._wl_ac.add(norm)
        self._has_whitelist = True

    def build(self):
        self._ac.build()
        if self._has_whitelist:
            self._wl_ac.build()

    def _boundary_ok(self, norm_text, start, end):
        if self._boundary in ("both", "left") and start > 0:
            if _is_word_char(norm_text[start - 1]):
                return False
        if self._boundary in ("both", "right") and end < len(norm_text):
            if _is_word_char(norm_text[end]):
                return False
        return True

    @staticmethod
    def _contained(spans, start, end):
        """spans 为按起点排序的白名单区间，判断 [start,end) 是否被完整包含。"""
        for s, e in spans:
            if s > start:
                break
            if s <= start and end <= e:
                return True
        return False

    def finditer(self, text):
        norm_text, index_map = normalize(text, **self._norm_opts)
        if not norm_text:
            return
        wl_spans = []
        if self._has_whitelist:
            wl_spans = sorted((s, e) for s, e, _ in self._wl_ac.finditer(norm_text))
        for start, end, word in self._ac.finditer(norm_text):
            if not self._boundary_ok(norm_text, start, end):
                continue
            if wl_spans and self._contained(wl_spans, start, end):
                continue
            o_start, o_end = span_to_original(index_map, start, end)
            yield Match(word=word, start=o_start, end=o_end,
                        text=text[o_start:o_end])

    def find_all(self, text):
        """返回按 (start, end, word) 排序的命中列表（稳定顺序，便于对拍）。"""
        return sorted(self.finditer(text),
                      key=lambda m: (m.start, m.end, m.word))
