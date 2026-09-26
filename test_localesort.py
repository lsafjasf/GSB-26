"""localesort 自测：全序断言、稳定性、对拍、边界情形、未覆盖报告。"""

import json
import random
import unittest
from functools import cmp_to_key

from localesort import LocaleSorter

CONFIG_PATH = "sort_rules.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def independent_key(config, text):
    """独立实现的排序键（测试专用，与库实现不共享代码），用于对拍。"""
    letters = config["letters"]
    cjk = {c: (e["pinyin"], e.get("tone", 0)) for c, e in config["cjk"].items()}
    symbols = config["symbols"]
    numeric = config["numeric"]
    level1, level2, level3 = [], [], []
    i = 0
    while i < len(text):
        ch = text[i]
        if numeric and ch.isdigit() and ch.isascii():
            j = i
            while j < len(text) and text[j].isdigit() and text[j].isascii():
                j += 1
            level1.append((1, int(text[i:j])))
            level2.append(0)
            level3.append(0)
            i = j
            continue
        if ch in letters:
            level1.append((2, letters[ch][0]))
            level2.append(letters[ch][1])
            level3.append(0 if not ch.isupper() else 1)
        elif ch in cjk:
            level1.append((2, cjk[ch][0]))
            level2.append(cjk[ch][1])
            level3.append(0)
        elif ch in symbols:
            level1.append((0, symbols[ch]))
            level2.append(0)
            level3.append(0)
        else:
            level1.append((9, ord(ch)))
            level2.append(0)
            level3.append(0)
        i += 1
    return (tuple(level1), tuple(level2), tuple(level3))


class EdgeCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sorter = LocaleSorter.from_file(CONFIG_PATH)

    def test_empty_and_single(self):
        self.assertEqual(self.sorter.sort([]), [])
        self.assertEqual(self.sorter.sort(["唯一"]), ["唯一"])
        self.assertEqual(self.sorter.sort([""]), [""])
        self.assertEqual(self.sorter.sort(["", "a", ""]), ["", "", "a"])

    def test_mixed_language(self):
        data = ["上海", "apple", "Ábc", "banana", "北京", "abc"]
        got = self.sorter.sort(data)
        # abc < Ábc(主级同 abc，次级变音靠后) < apple < banana < 北京(bei) < 上海(shang)
        self.assertEqual(got, ["abc", "Ábc", "apple", "banana", "北京", "上海"])

    def test_numeric_toggle(self):
        data = ["item10", "item2", "item1"]
        self.assertEqual(self.sorter.sort(data), ["item1", "item2", "item10"])
        cfg = load_config()
        cfg["numeric"] = False
        off = LocaleSorter(cfg)
        self.assertEqual(off.sort(data), ["item1", "item10", "item2"])
        # 数值相等时前导零不决定顺序（键相等 -> 稳定）
        self.assertEqual(self.sorter.sort(["a01", "a1"]), ["a01", "a1"])

    def test_multilevel(self):
        s = self.sorter
        # 主级：拼音/字母序
        self.assertLess(s.compare("安", "北"), 0)      # an < bei
        self.assertLess(s.compare("b", "北"), 0)      # b < bei
        # 次级：变音与声调
        self.assertLess(s.compare("a", "á"), 0)       # 无变音 < 尖音
        self.assertLess(s.compare("á", "à"), 0)       # 尖音 < 抑音
        self.assertLess(s.compare("苏", "肃"), 0)     # su1 < su4
        self.assertLess(s.compare("青", "庆"), 0)     # qing1 < qing4
        # 三级：大小写（主、次级都相同才比较）
        self.assertLess(s.compare("a", "A"), 0)
        self.assertLess(s.compare("á", "Á"), 0)
        self.assertLess(s.compare("A", "á"), 0)       # 次级优先于三级

    def test_symbols_and_digits(self):
        s = self.sorter
        self.assertLess(s.compare("a b", "ab"), 0)    # 空格位次 < 字母
        self.assertGreater(s.compare("a-b", "a b"), 0)  # 空格 rank 0 < '-' rank 1
        self.assertLess(s.compare("9", "a"), 0)       # 数字在字母前
        self.assertGreater(s.compare("10", "9a"), 0)  # 10 > 9（按数值）

    def test_unknown_fallback_end(self):
        s = self.sorter
        self.assertGreater(s.compare("龘", "zzzzz"), 0)   # 未知汉字排末尾
        self.assertGreater(s.compare("🙂", "zzzzz"), 0)   # emoji 排末尾
        self.assertLess(s.compare("€", "🙂"), 0)          # 未知之间按码位
        got = s.sort(["🙂b", "zz", "龘", "ab"])
        self.assertEqual(got, ["ab", "zz", "龘", "🙂b"])  # 龘(U+9F98) < 🙂(U+1F642)

    def test_polyphone(self):
        s = self.sorter
        # 多音字按声明的主读音排序：中(zhong1) < 重(zhong4) < 州(zhou1)
        self.assertEqual(s.sort(["州", "重", "中"]), ["中", "重", "州"])
        self.assertIn("重", s.polyphones)
        self.assertIn("chong2", s.polyphones["重"])
        self.assertIn("乐", s.polyphones)
        self.assertIn("yue4", s.polyphones["乐"])


class StabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sorter = LocaleSorter.from_file(CONFIG_PATH)

    def test_equal_keys_keep_original_order(self):
        # "a01" 与 "a1" 数值键相等；"苏" 重复出现键相等
        records = [("a01", 0), ("苏", 1), ("a1", 2), ("苏", 3), ("a1", 4)]
        got = sorted(records, key=lambda r: self.sorter.key(r[0]))
        self.assertEqual([r[1] for r in got], [0, 2, 4, 1, 3])

    def test_random_records_stable(self):
        rng = random.Random(7)
        pool = ["北京", "上海", "abc", "a1", "a01", "Áb"]
        records = [(rng.choice(pool), i) for i in range(2000)]
        got = sorted(records, key=lambda r: self.sorter.key(r[0]))
        for k in range(1, len(got)):
            ka = self.sorter.key(got[k - 1][0])
            kb = self.sorter.key(got[k][0])
            self.assertLessEqual(ka, kb)
            if ka == kb:
                self.assertLess(got[k - 1][1], got[k][1])


class TotalOrderTest(unittest.TestCase):
    """用随机数据断言比较器满足全序（自反、反对称、传递、与排序一致）。"""

    @classmethod
    def setUpClass(cls):
        cls.sorter = LocaleSorter.from_file(CONFIG_PATH)
        rng = random.Random(20260926)
        alphabet = (
            "abczáéÄÖü "
            "中重州苏肃安北上海"
            "0123456789.-_"
            "龘🙂€"
        )
        cls.samples = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
            for _ in range(400)
        ]

    def test_reflexive_and_antisymmetric(self):
        s = self.sorter
        for a in self.samples:
            self.assertEqual(s.compare(a, a), 0)
        rng = random.Random(1)
        for _ in range(4000):
            a = rng.choice(self.samples)
            b = rng.choice(self.samples)
            ab = s.compare(a, b)
            ba = s.compare(b, a)
            self.assertEqual((ab > 0) - (ab < 0), -((ba > 0) - (ba < 0)))

    def test_transitive(self):
        s = self.sorter
        rng = random.Random(2)
        checked = 0
        for _ in range(20000):
            a, b, c = (rng.choice(self.samples) for _ in range(3))
            if s.compare(a, b) <= 0 and s.compare(b, c) <= 0:
                self.assertLessEqual(s.compare(a, c), 0)
                checked += 1
        self.assertGreater(checked, 500)  # 确保样本有效

    def test_totality_and_sort_consistency(self):
        s = self.sorter
        rng = random.Random(3)
        for _ in range(4000):
            a = rng.choice(self.samples)
            b = rng.choice(self.samples)
            # 完全性：任意两者必可比较，且结果非 -1/0/1 之外
            self.assertIn((s.compare(a, b) > 0) - (s.compare(a, b) < 0), (-1, 0, 1))
        # 键排序与比较器排序结果一致
        data = random.Random(4).sample(self.samples, 200)
        by_key = s.sort(data)
        by_cmp = sorted(data, key=cmp_to_key(s.compare))
        self.assertEqual(by_key, by_cmp)
        # 键互不相同的集合：任意打乱后排序结果唯一
        distinct = list({s.key(x): x for x in self.samples}.values())
        base = s.sort(distinct)
        for seed in range(5):
            shuffled = distinct[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(s.sort(shuffled), base)


class CrossCheckTest(unittest.TestCase):
    """与配置声明的规则表逐项对拍。"""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.sorter = LocaleSorter(cls.config)

    def test_matches_declared_order(self):
        expected = list(self.config["expected_order"])
        for seed in range(10):
            shuffled = expected[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(self.sorter.sort(shuffled), expected)

    def test_matches_independent_implementation(self):
        cfg = self.config
        charset = list(cfg["expected_order"])
        rng = random.Random(11)
        for _ in range(20):
            sample = ["".join(rng.choice(charset) for _ in range(rng.randint(1, 5)))
                      for _ in range(200)]
            lib_order = self.sorter.sort(sample)
            ref_order = sorted(sample, key=lambda t: independent_key(cfg, t))
            self.assertEqual(lib_order, ref_order)


class UncoveredReportTest(unittest.TestCase):
    def test_report_lists_uncovered_chars(self):
        sorter = LocaleSorter.from_file(CONFIG_PATH)
        data = ["北京", "龘龖", "abc🙂", "€100", "北京龘"]
        report = sorter.uncovered_report(data)
        self.assertEqual(report.get("龘"), 2)
        self.assertEqual(report.get("龖"), 1)
        self.assertEqual(report.get("🙂"), 1)
        self.assertEqual(report.get("€"), 1)
        self.assertNotIn("北", report)
        self.assertNotIn("a", report)
        self.assertNotIn("1", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
