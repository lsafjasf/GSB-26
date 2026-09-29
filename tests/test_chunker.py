# -*- coding: utf-8 -*-
"""回归测试：先稳定复现四类现网缺陷，再验证修复后的行为。

运行：python3 -m unittest discover -s tests -v   （在仓库根目录）
"""

import os
import sys
import unicodedata
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chunker        # 修复后的实现
import chunker_buggy  # 有缺陷的实现（保留用于复现）

COMBINING_ACUTE = "́"          # 组合用锐音符
ZWJ = "‍"                     # 零宽连接符
SKIN_TONE = "🏽"              # 表情肤色修饰符
MIXED = "你好world世界123测试abc"


def grapheme_boundaries(text):
    """返回 text 中所有字素簇边界的下标集合（含 0 与 len(text)）。"""
    bounds = {0}
    pos = 0
    for g in chunker.iter_graphemes(text):
        pos += len(g)
        bounds.add(pos)
    return bounds


class BuggyReproductionTests(unittest.TestCase):
    """稳定复现四类现网问题（针对 chunker_buggy，断言缺陷确实存在）。"""

    def test_1_combining_mark_split_from_base(self):
        text = "cafe" + COMBINING_ACUTE  # e + 组合锐音符（分解形式）
        chunks = chunker_buggy.chunk_text(text, 4)
        self.assertEqual(chunks, ["cafe", COMBINING_ACUTE])
        # 第二个片段以孤立组合记号开头：变音符号与基字符被切成两半
        self.assertTrue(unicodedata.combining(chunks[1][0]))

    def test_2_emoji_sequence_torn_apart(self):
        text = "ok👍🏽!"  # 竖起大拇指 + 肤色修饰符
        chunks = chunker_buggy.chunk_text(text, 3)
        self.assertEqual(chunks, ["ok👍", "🏽!"])
        # 修饰符被拆成孤立码位，失去所修饰的表情
        self.assertEqual(chunks[1][0], SKIN_TONE)

    def test_3_zwj_left_at_chunk_end(self):
        text = "a👨‍👩b"  # a + 男人+ZWJ+女人 + b
        chunks = chunker_buggy.chunk_text(text, 3)
        self.assertEqual(chunks, ["a👨‍", "👩b"])
        self.assertTrue(chunks[0].endswith(ZWJ))  # ZWJ 残留在片段末尾

    def test_4_truncated_text_cannot_be_restored(self):
        text = "ab中文cd"
        head = chunker_buggy.truncate_bytes(text, 4)  # 切口落在"中"的字节中间
        self.assertEqual(head, "ab�")
        # 调用方按“截断结果是原文前缀”的假设取剩余部分重新拼接
        restored = head + text[len(head):]
        self.assertNotEqual(restored, text)  # 无法还原原文


class GraphemeBoundaryTests(unittest.TestCase):
    """修复后：边界判定覆盖变音符号、表情序列、ZWJ、国旗与退化输入。"""

    def test_combining_sequence_is_one_cluster(self):
        self.assertEqual(chunker.graphemes("éx"), ["é", "x"])

    def test_emoji_with_modifier_is_one_cluster(self):
        self.assertEqual(chunker.graphemes("👍🏽!"), ["👍🏽", "!"])

    def test_zwj_sequence_is_one_cluster(self):
        self.assertEqual(chunker.graphemes("👨‍👩‍👧x"), ["👨‍👩‍👧", "x"])

    def test_regional_indicators_pair_up(self):
        self.assertEqual(chunker.graphemes("🇨🇳🇺🇸🇯"), ["🇨🇳", "🇺🇸", "🇯"])

    def test_text_starting_with_incomplete_sequence(self):
        # 以孤立组合记号开头：该记号自成一个退化簇，不丢字
        text = "́ab"
        self.assertEqual("".join(chunker.graphemes(text)), text)
        self.assertEqual(chunker.graphemes(text)[0], "́")
        # 以 ZWJ 开头：并入其后的簇
        text2 = "‍ab"
        self.assertEqual("".join(chunker.graphemes(text2)), text2)

    def test_text_ending_with_incomplete_sequence(self):
        # 以 ZWJ 结尾：ZWJ 并入前一个簇，只有原文末尾才允许出现
        text = "ab‍"
        clusters = chunker.graphemes(text)
        self.assertEqual(clusters, ["a", "b‍"])
        chunks = chunker.chunk_text(text, 1)
        self.assertEqual("".join(chunks), text)
        for chunk in chunks[:-1]:
            self.assertFalse(chunk.endswith(ZWJ))

    def test_mixed_cjk_english(self):
        chunks = chunker.chunk_text(MIXED, 3)
        self.assertEqual("".join(chunks), MIXED)
        self.assertTrue(all(len(c) <= 3 for c in chunks))


class ConservationTests(unittest.TestCase):
    """内容守恒：拼接逐字符等于原文；片段边界必为字素边界。"""

    SAMPLES = [
        "",
        "a",
        "café naïve",
        "👨‍👩‍👧‍👦👍🏽🇨🇳",
        MIXED,
        "́开头孤立记号",
        "结尾ZWJ‍",
        "áb́ć混合mixed👩‍💻代码",
        (MIXED + "👨‍👩‍👧é🇨🇳") * 200,  # 较长混合文本
    ]

    def test_concat_equals_original_all_strategies(self):
        for text in self.SAMPLES:
            for strategy in ("codepoints", "width", "bytes"):
                for size in (1, 2, 3, 7, 50):
                    chunks = chunker.chunk_text(text, size, strategy=strategy)
                    self.assertEqual(
                        "".join(chunks), text,
                        msg="strategy=%r size=%r" % (strategy, size),
                    )

    def test_no_chunk_ends_with_incomplete_grapheme(self):
        for text in self.SAMPLES:
            bounds = grapheme_boundaries(text)
            for size in (1, 2, 5):
                pos = 0
                for chunk in chunker.chunk_text(text, size):
                    pos += len(chunk)
                    self.assertIn(pos, bounds)
        # 除原文自身结尾外，任何片段不得以 ZWJ 结尾
        text = "x👨‍👩‍👧y👍🏽z"
        for chunk in chunker.chunk_text(text, 2)[:-1]:
            self.assertFalse(chunk.endswith(ZWJ))
            self.assertFalse(unicodedata.combining(chunk[-1]))

    def test_truncate_round_trip_restores_original(self):
        text = "ab中文👨‍👩‍👧é🇨🇳tail"
        for strategy in ("codepoints", "width", "bytes"):
            for limit in range(0, 30):
                head = chunker.truncate(text, limit, strategy=strategy)
                self.assertTrue(text.startswith(head))
                self.assertEqual(head + text[len(head):], text)

    def test_buggy_input_also_conserved_by_fixed_impl(self):
        # 用缺陷复现用例的输入验证修复版
        for text in ("café", "ok👍🏽!", "a👨‍👩b", "ab中文cd"):
            chunks = chunker.chunk_text(text, 3)
            self.assertEqual("".join(chunks), text)
            head = chunker.truncate(text, 4, strategy="bytes")
            self.assertEqual(head + text[len(head):], text)


class StrategyTests(unittest.TestCase):
    """两种截断策略并存：codepoints 按字素数，width 按显示宽度。"""

    def test_width_strategy_counts_cjk_as_two(self):
        text = "你好世界"  # 每个字显示宽度 2
        self.assertEqual(chunker.truncate(text, 5, strategy="width"), "你好")
        # codepoints 策略下 limit 按字素个数计，4 个字素整体放得下
        self.assertEqual(chunker.truncate(text, 5, strategy="codepoints"), "你好世界")
        self.assertEqual(chunker.truncate(text, 2, strategy="codepoints"), "你好")

    def test_width_strategy_never_exceeds_budget(self):
        text = "ab中文cd👨‍👩‍👧ef"
        for limit in range(1, 20):
            head = chunker.truncate(text, limit, strategy="width")
            width = sum(chunker.grapheme_width(g) for g in chunker.iter_graphemes(head))
            self.assertLessEqual(width, limit)

    def test_zwj_sequence_counts_as_single_display_cell(self):
        self.assertEqual(chunker.grapheme_width("👨‍👩‍👧"), 2)
        self.assertEqual(chunker.truncate("👨‍👩‍👧ab", 2, strategy="width"), "👨‍👩‍👧")

    def test_strategies_differ_on_same_input(self):
        text = "中文ab"
        # 同为 limit=3：宽度策略只能放下一个汉字，字素策略能放三个字素
        self.assertEqual(chunker.truncate(text, 3, strategy="width"), "中")
        self.assertEqual(chunker.truncate(text, 3, strategy="codepoints"), "中文a")

    def test_invalid_strategy_rejected(self):
        with self.assertRaises(ValueError):
            chunker.truncate("abc", 2, strategy="bogus")
        with self.assertRaises(ValueError):
            chunker.chunk_text("abc", 2, strategy="bogus")


class WidthBudgetTests(unittest.TestCase):
    """宽度口径：修饰符不额外占宽；分块/截断与上限的关系被显式约定。"""

    def test_modifier_cluster_width_not_double_counted(self):
        # 👍🏽 整体渲染为一个字形：宽 2，而不是 2+2=4
        self.assertEqual(chunker.grapheme_width("👍🏽"), 2)
        self.assertEqual(chunker.grapheme_width("👍"), 2)
        # 分块：size=2 时每片恰好放一个带修饰符的簇，不再被挤成空片
        self.assertEqual(
            chunker.chunk_text("👍🏽👍🏽", 2, strategy="width"),
            ["👍🏽", "👍🏽"],
        )
        # 截断：limit=2 能放下整个簇
        self.assertEqual(chunker.truncate("👍🏽!", 2, strategy="width"), "👍🏽")

    def test_chunk_single_oversized_cluster_is_declared_exception(self):
        # 显式例外：簇不可拆分，单个超宽簇独占一个片段并超出 size
        chunks = chunker.chunk_text("中a", 1, strategy="width")
        self.assertEqual(chunks, ["中", "a"])
        self.assertGreater(chunker.grapheme_width(chunks[0]), 1)
        # 不变量：任何超出 size 的片段都必须是且仅是单个字素簇
        text = "中a👨‍👩‍👧b👍🏽c"
        for size in (1, 2):
            for chunk in chunker.chunk_text(text, size, strategy="width"):
                width = sum(
                    chunker.grapheme_width(g)
                    for g in chunker.iter_graphemes(chunk)
                )
                if width > size:
                    self.assertEqual(chunker.graphemes(chunk), [chunk])

    def test_truncate_never_exceeds_even_with_oversized_head(self):
        # 截断没有例外：首簇就超预算时整体丢弃，结果绝不超出 limit
        self.assertEqual(chunker.truncate("中文", 1, strategy="width"), "")
        self.assertEqual(chunker.truncate("👨‍👩‍👧ab", 1, strategy="width"), "")
        self.assertEqual(chunker.truncate("👍🏽ab", 1, strategy="width"), "")


if __name__ == "__main__":
    unittest.main()
