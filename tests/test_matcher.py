"""单元测试：命中规则、归一化位置映射、边界、白名单、误伤样本。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sensitive_matcher import SensitiveMatcher, normalize, span_to_original


def make(words, **kw):
    m = SensitiveMatcher(**kw)
    m.add_all(words)
    m.build()
    return m


def spans(matches):
    return sorted(((m.word, m.start, m.end) for m in matches),
              key=lambda t: (t[1], t[2], t[0]))


class TestOverlap(unittest.TestCase):
    def test_prefix_of_another_word(self):
        # 一个词是另一个词的前缀：两者都必须命中
        m = make(["he", "her", "hers"])
        self.assertEqual(
            spans(m.find_all("hers")),
            [("he", 0, 2), ("her", 0, 3), ("hers", 0, 4)],
        )

    def test_suffix_overlap_via_fail(self):
        # "she" 中同时命中 "she" 与后缀 "he"
        m = make(["she", "he"])
        self.assertEqual(spans(m.find_all("she")),
                         [("she", 0, 3), ("he", 1, 3)])

    def test_same_start_multiple_words(self):
        m = make(["abcd", "ab", "abc"])
        self.assertEqual(spans(m.find_all("abcd")),
                         [("ab", 0, 2), ("abc", 0, 3), ("abcd", 0, 4)])

    def test_chained_overlap(self):
        m = make(["aaa", "aa"])
        # "aaaa": aa@[0,2) aa@[1,3) aa@[2,4) aaa@[0,3) aaa@[1,4)
        self.assertEqual(
            spans(m.find_all("aaaa")),
            [("aa", 0, 2), ("aaa", 0, 3), ("aa", 1, 3),
             ("aaa", 1, 4), ("aa", 2, 4)],
        )

    def test_cjk(self):
        m = make(["赌博", "博采", "赌"])
        self.assertEqual(spans(m.find_all("去赌博采风")),
                         [("赌", 1, 2), ("赌博", 1, 3), ("博采", 2, 4)])


class TestNormalizePosition(unittest.TestCase):
    def roundtrip(self, text, words, **kw):
        m = make(words, **kw)
        hits = m.find_all(text)
        for h in hits:
            # 位置必须对应原文偏移
            self.assertEqual(text[h.start:h.end], h.text)
        return hits

    def test_lowercase_position(self):
        hits = self.roundtrip("xxAbCdyy", ["abcd"], lowercase=True)
        self.assertEqual(spans(hits), [("abcd", 2, 6)])
        self.assertEqual(hits[0].text, "AbCd")

    def test_fullwidth_position(self):
        # 全角 ＡＢＣ 命中半角词 abc，位置指向原文全角字符
        hits = self.roundtrip("说ＡＢＣ吧", ["abc"],
                              lowercase=True, fullwidth_to_halfwidth=True)
        self.assertEqual(spans(hits), [("abc", 1, 4)])
        self.assertEqual(hits[0].text, "ＡＢＣ")

    def test_zero_width_position(self):
        # 零宽字符插入词中间，命中区间应覆盖原文（含零宽字符）
        text = "赌​博"
        hits = self.roundtrip(text, ["赌博"], strip_zero_width=True)
        self.assertEqual(spans(hits), [("赌博", 0, 3)])
        self.assertEqual(hits[0].text, "赌​博")

    def test_whitespace_position(self):
        text = "赌  博\n吧"
        hits = self.roundtrip(text, ["赌博"], strip_whitespace=True)
        self.assertEqual(spans(hits), [("赌博", 0, 4)])
        self.assertEqual(hits[0].text, "赌  博")

    def test_casefold_expansion(self):
        # 'ß' casefold -> 'ss'：一个原文字符展开为两个归一化字符
        hits = self.roundtrip("STRASSE", ["strasse"], lowercase=True)
        self.assertEqual(spans(hits), [("strasse", 0, 7)])

    def test_combined(self):
        text = "ＡＢ​ ｃＤ"
        hits = self.roundtrip(text, ["abcd"], lowercase=True,
                              fullwidth_to_halfwidth=True,
                              strip_whitespace=True, strip_zero_width=True)
        self.assertEqual(spans(hits), [("abcd", 0, 6)])

    def test_normalize_mapping_api(self):
        norm, idx = normalize("AＢ C", lowercase=True,
                              fullwidth_to_halfwidth=True,
                              strip_whitespace=True)
        self.assertEqual(norm, "abc")
        self.assertEqual(idx, [0, 1, 3])
        self.assertEqual(span_to_original(idx, 0, 3), (0, 4))


class TestBoundary(unittest.TestCase):
    TEXT = "class assassin pass the ass at last"

    def test_no_boundary_has_false_positives(self):
        m = make(["ass"])
        self.assertEqual(spans(m.find_all(self.TEXT)),
                         [("ass", 2, 5), ("ass", 6, 9), ("ass", 9, 12),
                          ("ass", 16, 19), ("ass", 24, 27)])

    def test_boundary_kills_false_positives(self):
        m = make(["ass"], boundary="both")
        # 只剩独立成词的 "ass"（24..27）
        self.assertEqual(spans(m.find_all(self.TEXT)), [("ass", 24, 27)])

    def test_left_right_boundary(self):
        m = make(["ass"], boundary="left")
        got = spans(m.find_all(self.TEXT))
        # 左边界放行：独立词 (24,27) 与句首空格后的 assassin 前缀 (6,9)
        self.assertEqual(got, [("ass", 6, 9), ("ass", 24, 27)])
        m2 = make(["ass"], boundary="right")
        got2 = spans(m2.find_all(self.TEXT))
        # 右边界放行：class 中的 (2,5)、pass 中的 (16,19)、独立词 (24,27)
        self.assertEqual(got2, [("ass", 2, 5), ("ass", 16, 19),
                                ("ass", 24, 27)])

    def test_boundary_on_normalized_text(self):
        # 边界判定在归一化文本上进行：大小写不影响判定
        m = make(["ass"], boundary="both", lowercase=True)
        self.assertEqual(spans(m.find_all("an ASS here")), [("ass", 3, 6)])


class TestWhitelist(unittest.TestCase):
    def test_whitelist_suppresses_contained(self):
        m = make(["木马"], whitelist=["旋转木马"])
        self.assertEqual(m.find_all("坐旋转木马去"), [])
        self.assertEqual(spans(m.find_all("这是木马病毒")), [("木马", 2, 4)])

    def test_partial_overlap_not_suppressed(self):
        # 只有被完整包含才抑制，部分重叠不抑制
        m = make(["木马拉"], whitelist=["旋转木马"])
        self.assertEqual(spans(m.find_all("旋转木马拉萨")),
                         [("木马拉", 2, 5)])

    def test_whitelist_with_normalization(self):
        m = make(["ass"], whitelist=["classic"], lowercase=True)
        self.assertEqual(m.find_all("a CLASSIC example"), [])
        self.assertEqual(spans(m.find_all("a classic ass")),
                         [("ass", 10, 13)])


class TestFalsePositiveSamples(unittest.TestCase):
    """刻意构造的误伤样本（Scunthorpe 问题）。"""

    BENIGN = [
        "I passed the class with assistance.",
        "Sussex and Essex are counties in England.",
        "The bass guitar is classic.",
        "我们周末去坐旋转木马。",
        "他用玩具水枪打水仗。",
        "这句话里有赌博网站链接",  # 真正的命中（对照组）
    ]

    def test_false_positive_control(self):
        sensitive = ["ass", "sex", "木马", "水枪", "赌博"]
        # 无防护：全部误伤都命中
        raw = make(sensitive)
        raw_hits = {i: raw.find_all(t) for i, t in enumerate(self.BENIGN)}
        self.assertTrue(raw_hits[0] and raw_hits[1] and raw_hits[2])
        self.assertTrue(raw_hits[3] and raw_hits[4])
        # 边界 + 白名单：误伤清零，真命中保留
        guarded = SensitiveMatcher(boundary="both",
                                   whitelist=["旋转木马", "水枪"])
        guarded.add_all(sensitive)
        guarded.build()
        for i in range(5):
            self.assertEqual(guarded.find_all(self.BENIGN[i]), [],
                             f"sample {i} should be clean")
        self.assertEqual(spans(guarded.find_all(self.BENIGN[5])),
                         [("赌博", 5, 7)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
