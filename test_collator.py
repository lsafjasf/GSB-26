"""区域化排序库自测（unittest，仅标准库）。

覆盖：
  - 空集合 / 单元素 / 混合语言 / 数字与符号
  - 多级比较（主级字母、次级变音、三级大小写）
  - 数字串按数值比较的开关
  - 稳定性（相同排序键保持原始相对顺序）
  - 全序断言（随机数据：自反、反对称、传递、可比性）
  - 与规则表逐项对拍（expected_order_groups）
  - 未知字符回退位置与未覆盖字符报告
"""

import json
import random
import unittest

from collator import Collator


def load_collator(**overrides):
    with open("rules.json", encoding="utf-8") as f:
        config = json.load(f)
    config.update(overrides)
    return Collator(config)


class TestBasicCases(unittest.TestCase):
    def setUp(self):
        self.collator = load_collator()

    def test_empty_and_single(self):
        self.assertEqual(self.collator.sort([]), [])
        self.assertEqual(self.collator.sort(["张"]), ["张"])
        self.assertEqual(self.collator.sort([""]), [""])

    def test_empty_string_sorts_first(self):
        self.assertEqual(self.collator.sort(["b", "", "a"]), ["", "a", "b"])

    def test_mixed_language(self):
        items = ["banana", "苹果", "apple", "香蕉", "Ápple"]
        # 苹果/香蕉 未在映射表中 -> 回退到末尾（按码位确定顺序）
        result = self.collator.sort(items)
        self.assertEqual(result[:3], ["apple", "Ápple", "banana"])
        self.assertEqual(set(result[3:]), {"苹果", "香蕉"})
        self.assertLess(ord(result[3][0]), ord(result[4][0]))

    def test_multilevel_latin(self):
        # 主级相同 -> 次级变音 -> 三级大小写
        self.assertEqual(self.collator.sort(["à", "A", "á", "a"]), ["a", "A", "á", "à"])
        self.assertEqual(self.collator.sort(["ç", "c", "C"]), ["c", "C", "ç"])
        self.assertLess(self.collator.compare("resume", "résumé"), 0)

    def test_chinese_pinyin_order(self):
        items = ["张", "李", "安", "王"]
        self.assertEqual(self.collator.sort(items), ["安", "李", "王", "张"])

    def test_chinese_tone_as_secondary(self):
        self.assertEqual(self.collator.sort(["爸", "八", "把", "拔"]),
                         ["八", "拔", "把", "爸"])

    def test_digits_and_symbols(self):
        items = ["item-2", "item-10", "item 1", "item_3"]
        self.assertEqual(self.collator.sort(items),
                         ["item 1", "item-2", "item-10", "item_3"])

    def test_numeric_toggle(self):
        on = load_collator()
        off = load_collator(numeric_collation=False)
        self.assertEqual(on.sort(["a10", "a2", "a1"]), ["a1", "a2", "a10"])
        self.assertEqual(off.sort(["a10", "a2", "a1"]), ["a1", "a10", "a2"])

    def test_numeric_large_numbers(self):
        items = ["x1000000000000", "x9", "x100"]
        self.assertEqual(self.collator.sort(items), ["x9", "x100", "x1000000000000"])

    def test_superscript_digit_does_not_abort_sort(self):
        # ²(U+00B2)/³(U+00B3) 是数字字符（isdigit 为真）但不是 \d 能匹配的
        # 十进制数字，int() 会抛 ValueError：必须降级为字符比较，整批排序照常完成。
        items = ["a10", "a²", "x1²", "a1", "a2", "²", "³"]
        result = self.collator.sort(items)
        self.assertEqual(result, ["a1", "a2", "a10", "a²", "x1²", "²", "³"])
        # 覆盖判定与切分口径一致：上标数字不算数值数字，且不在映射表中
        self.assertFalse(self.collator.is_covered("²"))
        self.assertEqual(self.collator.uncovered_chars(["x²"]), {"²": 1})

    def test_stability(self):
        # 相同排序键的记录保持原始相对顺序
        records = [("a", 0), ("A", 1), ("a", 2), ("A", 3), ("á", 4), ("a", 5)]
        # a 与 A 主/次级相同，仅三级不同 -> 不同键；构造真正同键的重复项
        records = [("an", 0), ("安", 1), ("an", 2), ("安", 3), ("an", 4)]
        result = self.collator.sort(records, key=lambda r: r[0])
        self.assertEqual([r[1] for r in result[:3]], [0, 2, 4])
        self.assertEqual([r[1] for r in result[3:]], [1, 3])
        # 同键重复项（完全相同字符串）也保持稳定
        dup = [("ab", i) for i in range(10)]
        self.assertEqual([r[1] for r in self.collator.sort(dup, key=lambda r: r[0])],
                         list(range(10)))

    def test_unknown_fallback_end_and_deterministic(self):
        items = ["b", "Ω", "中", "€"]
        result = self.collator.sort(items)
        self.assertEqual(result[:2], ["b", "中"])   # b < zhong
        self.assertEqual(result[2:], ["Ω", "€"])    # 未知字符按码位排在末尾
        collator_start = load_collator(fallback="start")
        result2 = collator_start.sort(items)
        self.assertEqual(result2[:2], ["Ω", "€"])   # fallback=start 时排在开头

    def test_uncovered_report(self):
        strings = ["你好", "abc", "日本語", "a1", "你"]
        report = self.collator.uncovered_chars(strings)
        self.assertEqual(report, {"你": 2, "語": 1})  # 好/日/本 已在映射表中


class TestAgainstRulesTable(unittest.TestCase):
    """与规则表逐项对拍：配置声明的顺序必须与实际排序结果一致。"""

    def test_expected_order_groups(self):
        with open("rules.json", encoding="utf-8") as f:
            config = json.load(f)
        collator = Collator(config)
        rng = random.Random(20260926)
        for group in config["expected_order_groups"]:
            shuffled = list(group)
            rng.shuffle(shuffled)
            self.assertEqual(collator.sort(shuffled), group,
                             "分组对拍失败: %r" % (group,))

    def test_every_mapped_char_pair_consistent(self):
        # 对拍加强版：映射表内任意两字符，compare 的符号必须与排序键一致，
        # 且全表排序后相邻元素严格递增（无重复键冲突之外的异常）。
        collator = load_collator()
        chars = sorted(collator.mappings)
        ordered = collator.sort(chars)
        for i in range(len(ordered) - 1):
            self.assertLessEqual(collator.compare(ordered[i], ordered[i + 1]), 0)
            self.assertGreaterEqual(collator.compare(ordered[i + 1], ordered[i]), 0)


class TestTotalOrder(unittest.TestCase):
    """用随机数据断言比较器满足全序。"""

    @classmethod
    def setUpClass(cls):
        cls.collator = load_collator()
        rng = random.Random(42)
        alphabet = list("aAbBcCáàéçdDeE0123456789 -_.") + \
                   list("安八拔把爸才大江李马南青人王西一张") + \
                   ["Ω", "€", "你", "好", "ß", "日"]
        cls.strings = ["".join(rng.choice(alphabet)
                               for _ in range(rng.randint(0, 8)))
                       for _ in range(60)]
        cls.rng = rng

    def test_reflexive_and_antisymmetric(self):
        cmp = self.collator.compare
        for a in self.strings:
            self.assertEqual(cmp(a, a), 0)
        for a in self.strings:
            for b in self.strings:
                self.assertEqual(cmp(a, b), -cmp(b, a), "反对称性失败: %r %r" % (a, b))

    def test_transitive(self):
        cmp = self.collator.compare
        n = len(self.strings)
        for _ in range(300000):
            a, b, c = (self.strings[self.rng.randrange(n)] for _ in range(3))
            if cmp(a, b) <= 0 and cmp(b, c) <= 0:
                self.assertLessEqual(cmp(a, c), 0,
                                     "传递性失败: %r %r %r" % (a, b, c))

    def test_totality(self):
        # 任意两者必可比较，且 cmp==0 当且仅当排序键相等
        for a in self.strings:
            for b in self.strings:
                result = self.collator.compare(a, b)
                self.assertIn(result, (-1, 0, 1))
                self.assertEqual(result == 0,
                                 self.collator.sort_key(a) == self.collator.sort_key(b))

    def test_sorted_matches_comparator(self):
        import functools
        expected = self.collator.sort(self.strings)
        via_cmp = sorted(self.strings,
                         key=functools.cmp_to_key(self.collator.compare))
        self.assertEqual(expected, via_cmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
