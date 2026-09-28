"""单元测试：重叠命中、归一化位置映射、边界、白名单。"""

import unittest

from sensitive import (
    DEFAULT_CONFIG,
    RAW_CONFIG,
    NormalizeConfig,
    SensitiveEngine,
)
from sensitive.normalize import WS_KEEP, WS_REMOVE, Normalizer


class TestAhoCorasickOverlaps(unittest.TestCase):
    def test_classic_overlaps_and_prefix(self):
        # 经典 ushers：she/he/hers 同时出现
        eng = SensitiveEngine(["he", "she", "his", "hers"])
        words = [(h.word, h.start, h.end) for h in eng.find_all("ushers")]
        self.assertEqual(
            words,
            [
                ("she", 1, 4),  # u[she]rs
                ("he", 2, 4),  # us[he]rs
                ("hers", 2, 6),  # us[hers]
            ],
        )

    def test_same_start_multiple_matches_sorted_short_first(self):
        eng = SensitiveEngine(["a", "ab", "abc", "abcd"])
        words = [(h.word, h.start, h.end) for h in eng.find_all("xabcd")]
        self.assertEqual(
            words,
            [("a", 1, 2), ("ab", 1, 3), ("abc", 1, 4), ("abcd", 1, 5)],
        )

    def test_repeated_and_overlapping(self):
        # 全部重叠：aaa 中两次 aa
        eng = SensitiveEngine(["aa"])
        spans = [(h.start, h.end) for h in eng.find_all("aaaa")]
        self.assertEqual(spans, [(0, 2), (1, 3), (2, 4)])

    def test_suffix_only_match_via_dict_link(self):
        # 通过字典后缀链命中的情形
        eng = SensitiveEngine(["xyz", "yz"])
        words = sorted((h.word, h.start) for h in eng.find_all("abcxyz"))
        self.assertEqual(words, [("xyz", 3), ("yz", 4)])


class TestNormalization(unittest.TestCase):
    def test_casefold(self):
        eng = SensitiveEngine(["hello"])
        hits = eng.find_all("HELLO Hello hello")
        self.assertEqual([(h.start, h.end) for h in hits], [(0, 5), (6, 11), (12, 17)])

    def test_fullwidth_to_halfwidth(self):
        eng = SensitiveEngine(["abc", "ＡＢＣ"])  # 归一化后为同一词
        self.assertEqual(eng.word_count, 1)
        hits = eng.find_all("ＡｂＣ和abc")
        self.assertEqual([(h.start, h.end) for h in hits], [(0, 3), (4, 7)])

    def test_zero_width_removal_and_mapping(self):
        eng = SensitiveEngine(["法轮"])
        text = "练习法\u200b轮功"
        hits = eng.find_all(text)
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual((h.start, h.end), (2, 5))
        # 原文区间包含被删除的零宽字符
        self.assertEqual(text[h.start:h.end], "法\u200b轮")

    def test_whitespace_collapse(self):
        eng = SensitiveEngine(["敏感词"])
        # 全角空格 + 连续空白折叠后仍带一个空格，故 "敏  感词" 不命中
        self.assertEqual(eng.find_all("这是敏　感词"), [])
        # 词内部不带空格的正常命中
        self.assertEqual(len(eng.find_all("这是敏感词。")), 1)

    def test_whitespace_remove_mode(self):
        eng = SensitiveEngine(
            ["敏感词"],
            config=NormalizeConfig(whitespace=WS_REMOVE),
        )
        text = "这是敏 感\u3000词!"
        hits = eng.find_all(text)
        self.assertEqual(len(hits), 1)
        self.assertEqual(text[hits[0].start:hits[0].end], "敏 感\u3000词")

    def test_whitespace_keep_mode(self):
        eng = SensitiveEngine(
            ["abc"],
            config=NormalizeConfig(whitespace=WS_KEEP),
        )
        # keep 模式（但 width 仍开）：全角空格变普通空格，不折叠
        self.assertEqual(len(eng.find_all("a\u3000bc")), 0)
        self.assertEqual(len(eng.find_all("a bc")), 0)

    def test_position_map_basic(self):
        norm = Normalizer(DEFAULT_CONFIG).normalize("Ａｂ\u200bＣ x")
        self.assertEqual(norm.text, "abc x")
        self.assertEqual(norm.orig_pos, (0, 1, 3, 4, 5))
        self.assertEqual(norm.to_orig_span(0, 3), (0, 4))

    def test_raw_config(self):
        eng = SensitiveEngine(["ABC"], config=RAW_CONFIG)
        self.assertEqual(eng.find_all("abc"), [])
        self.assertEqual(len(eng.find_all("ABC")), 1)

    def test_empty_pattern_skipped(self):
        eng = SensitiveEngine(["\u200b", "abc"])
        self.assertEqual(eng.word_count, 1)
        self.assertEqual(eng.skipped_words, ("\u200b",))

    def test_t2s_traditional_evasion_and_position(self):
        eng = SensitiveEngine(["赌场", "办证大厅"])
        text = "這家地下賭場，去辦證大廳辦事"
        hits = [(h.word, h.start, h.end) for h in eng.find_all(text)]
        self.assertIn(("赌场", 4, 6), hits)
        self.assertIn(("办证大厅", 8, 12), hits)
        # 原文区间即繁体字形本身
        self.assertEqual(text[4:6], "賭場")

    def test_t2s_can_be_disabled(self):
        eng = SensitiveEngine(
            ["赌场"], config=NormalizeConfig(t2s=False)
        )
        self.assertEqual(eng.find_all("地下賭場"), [])
        # 简体仍可命中
        self.assertEqual(len(eng.find_all("地下赌场")), 1)

    def test_t2s_is_strict_one_to_one_only(self):
        # 不在 1:1 表中的字（如异体/一对多）保持原样，不做词级猜测
        norm = Normalizer(DEFAULT_CONFIG)
        # 「乾」属一对多（干/乾），表内不应收录 -> 保持原字
        self.assertEqual(norm.normalize_pattern("乾"), "乾")

    def test_homophone_evasion_caught_when_enabled(self):
        eng = SensitiveEngine(
            ["攻击"], config=NormalizeConfig(homophone=True)
        )
        # 功 gōng 被同音字 供 gōng 替换；折叠后命中
        hits = [(h.word, h.start, h.end) for h in eng.find_all("遭供击了")]
        self.assertEqual(hits, [("攻击", 1, 3)])

    def test_homophone_off_by_default(self):
        eng = SensitiveEngine(["攻击"])
        self.assertEqual(eng.find_all("遭供击了"), [])

    def test_homophone_uses_tone_sensitive_groups(self):
        # 法 fǎ 同音组只有自身；伐 fá（不同声调）不折叠为法
        eng = SensitiveEngine(
            ["法"], config=NormalizeConfig(homophone=True)
        )
        self.assertEqual(eng.find_all("伐木"), [])
        self.assertEqual(len(eng.find_all("法国")), 1)

    def test_homophone_custom_table(self):
        table = {"x": "a", "y": "a"}  # 自定义：x/y 都折叠到 a
        cfg = NormalizeConfig(
            homophone=True, homophone_table=table, t2s=False,
            width=False, casefold=False, zero_width=False, whitespace="keep",
        )
        eng = SensitiveEngine(["ab"], config=cfg)
        self.assertEqual(len(eng.find_all("xb")), 1)
        self.assertEqual(len(eng.find_all("yb")), 1)


class TestBoundary(unittest.TestCase):
    def test_ascii_boundary(self):
        eng = SensitiveEngine(["cat"], use_boundary=True)
        hits = eng.find_all("cat scatter cat! bobcat.")
        # 仅独立的两个 cat；scatter / bobcat 中的不报
        self.assertEqual([(h.start, h.end) for h in hits], [(0, 3), (12, 15)])

    def test_boundary_off_reports_all(self):
        eng = SensitiveEngine(["cat"], use_boundary=False)
        hits = eng.find_all("scatter")
        self.assertEqual([(h.start, h.end) for h in hits], [(1, 4)])

    def test_cjk_still_matches_with_boundary(self):
        eng = SensitiveEngine(["天安门"], use_boundary=True)
        hits = eng.find_all("我爱天安门广场")
        self.assertEqual([(h.start, h.end) for h in hits], [(2, 5)])

    def test_zero_width_between_chars_blocks_with_boundary(self):
        # 删除零宽后两词字符相邻，边界应生效
        eng = SensitiveEngine(["cat"], use_boundary=True)
        self.assertEqual(eng.find_all("s\u200bcatter"), [])


class TestWhitelist(unittest.TestCase):
    def test_whitelist_covers_sensitive(self):
        eng = SensitiveEngine(["天安门"], whitelist=["天安门广场"])
        hits = eng.find_all("去天安门广场，再见天安门")
        self.assertEqual([(h.start, h.end) for h in hits], [(9, 12)])

    def test_whitelist_must_cover_full_span(self):
        # 白名单短于敏感词不构成覆盖
        eng = SensitiveEngine(["天安门广场"], whitelist=["天安门"])
        self.assertEqual(len(eng.find_all("我爱天安门广场")), 1)

    def test_multiple_overlapping_whitelist_entries(self):
        eng = SensitiveEngine(["a", "ab"], whitelist=["xab"])
        hits = [(h.word, h.start) for h in eng.find_all("xabxa")]
        self.assertEqual(hits, [("a", 4)])


if __name__ == "__main__":
    unittest.main()
