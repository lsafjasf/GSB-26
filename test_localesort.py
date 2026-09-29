"""localesort 自测：全序断言、稳定性、对拍、边界情形、未覆盖报告、
配置校验、规则完整性检查、未覆盖字符处理策略。"""

import json
import random
import unittest
from functools import cmp_to_key

from localesort import (
    ConfigError,
    LocaleSorter,
    UncoveredCharError,
    validate_config,
)

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


class ConfigValidationTest(unittest.TestCase):
    """自定义配置校验：合法配置通过，非法配置给出可定位的问题。"""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config()

    def test_shipped_config_is_valid(self):
        self.assertEqual(validate_config(self.config), [])
        # 构造 sorter 不抛异常
        LocaleSorter(self.config)

    def test_rejects_bad_enum_values(self):
        for key, bad in (("case_first", "uppercase"),
                         ("unknown_position", "middle"),
                         ("uncovered_policy", "ignore")):
            cfg = dict(self.config)
            cfg[key] = bad
            issues = validate_config(cfg)
            self.assertTrue(any(key in msg for msg in issues), issues)
            with self.assertRaises(ConfigError):
                LocaleSorter(cfg)

    def test_rejects_cross_table_overlap(self):
        cfg = dict(self.config)
        cfg["symbols"] = dict(cfg["symbols"])
        cfg["symbols"]["a"] = 9  # 'a' 已在 letters 中
        issues = validate_config(cfg)
        self.assertTrue(any("同时出现" in msg for msg in issues), issues)
        with self.assertRaises(ConfigError):
            LocaleSorter(cfg)

    def test_rejects_duplicate_symbol_rank(self):
        cfg = dict(self.config)
        cfg["symbols"] = dict(cfg["symbols"])
        cfg["symbols"]["#"] = 0  # 位次 0 已被空格占用
        issues = validate_config(cfg)
        self.assertTrue(any("重复占用" in msg for msg in issues), issues)

    def test_rejects_bad_cjk_entry(self):
        cfg = dict(self.config)
        cfg["cjk"] = dict(cfg["cjk"])
        cfg["cjk"]["测试"] = {"pinyin": "ce", "tone": 1}  # 键非单字符
        cfg["cjk"]["测"] = {"pinyin": "", "tone": 9}      # 空拼音 + 声调越界
        issues = validate_config(cfg)
        self.assertTrue(any("单字符" in msg for msg in issues), issues)
        self.assertTrue(any("pinyin" in msg for msg in issues), issues)
        self.assertTrue(any("tone" in msg for msg in issues), issues)


class CoverageCheckTest(unittest.TestCase):
    """规则完整性检查：报告未覆盖类别，结论可复算。"""

    @classmethod
    def setUpClass(cls):
        cls.sorter = LocaleSorter.from_file(CONFIG_PATH)
        cls.report = cls.sorter.coverage_check()

    def test_report_is_reproducible(self):
        again = self.sorter.coverage_check()
        self.assertEqual(self.report, again)
        self.assertEqual(self.report["digest"], again["digest"])
        self.assertEqual(len(self.report["digest"]), 64)  # sha256 hex

    def test_uncovered_categories_are_actually_uncovered(self):
        covered = self.sorter.covered_chars()
        for entry in self.report["uncovered_categories"]:
            self.assertGreater(entry["uncovered"], 0)
            for ch in entry["samples"]:
                self.assertNotIn(ch, covered)

    def test_known_gaps_are_reported(self):
        # 配置未声明假名/emoji/货币符号：Lo(部分)、So、Sc 应出现在缺口里
        cats = {e["category"] for e in self.report["uncovered_categories"]}
        self.assertIn("So", cats)   # 其他符号（emoji 等）
        self.assertIn("Sc", cats)   # 货币符号（€ 等）
        # 已覆盖类别内计数应与配置一致
        self.assertEqual(
            self.report["categories"]["Nd"]["covered"], 10)  # ASCII 数字

    def test_format_is_deterministic(self):
        text1 = self.sorter.format_coverage_report(self.report)
        text2 = self.sorter.format_coverage_report()
        self.assertEqual(text1, text2)
        self.assertIn(self.report["digest"], text1)


class UncoveredPolicyTest(unittest.TestCase):
    """未覆盖字符处理策略：codepoint / error / fold。"""

    DATA = ["文件2", "apple", "ångström", "abc", "龘", "€9", "文件10", "Ábc"]

    @classmethod
    def setUpClass(cls):
        cls.base = load_config()

    def make_sorter(self, policy):
        cfg = dict(self.base)
        cfg["uncovered_policy"] = policy
        return LocaleSorter(cfg)

    def assert_total_order(self, sorter, data):
        for a in data:
            self.assertEqual(sorter.compare(a, a), 0)
            for b in data:
                ab = sorter.compare(a, b)
                ba = sorter.compare(b, a)
                self.assertEqual((ab > 0) - (ab < 0), -((ba > 0) - (ba < 0)))

    def test_codepoint_policy_is_default_and_unchanged(self):
        sorter = self.make_sorter("codepoint")
        got = sorter.sort(self.DATA)
        # 未覆盖字符（文/件/龘/€/å）回退到末尾按码位：å<€<件<文<龘
        self.assertEqual(got[:3], ["abc", "Ábc", "apple"])
        self.assertEqual(got[3:], ["ångström", "€9", "文件2", "文件10", "龘"])
        self.assert_total_order(sorter, self.DATA)

    def test_error_policy_raises_with_location(self):
        sorter = self.make_sorter("error")
        # 全覆盖数据正常工作
        self.assertEqual(sorter.sort(["abc", "Ábc", "apple"]),
                         ["abc", "Ábc", "apple"])
        with self.assertRaises(UncoveredCharError) as ctx:
            sorter.sort(self.DATA)
        self.assertIn("U+", str(ctx.exception))

    def test_fold_policy_folds_into_equivalence_class(self):
        sorter = self.make_sorter("fold")
        got = sorter.sort(self.DATA)
        # å 折叠进 a 等价类（an..<ap.. 故在 apple 前）；文/件/龘/€ 无法折叠仍回退码位
        self.assertEqual(got[:4], ["abc", "Ábc", "ångström", "apple"])
        self.assertEqual(got[4:], ["€9", "文件2", "文件10", "龘"])
        # 折叠确定性：同一等价类内原字符 < 折叠字符
        self.assertLess(sorter.compare("a", "å"), 0)
        self.assert_total_order(sorter, self.DATA)

    def test_policy_switch_is_stable_and_explainable(self):
        # 同一批数据、同一策略：重复排序结果一致
        for policy in ("codepoint", "fold"):
            sorter = self.make_sorter(policy)
            first = sorter.sort(self.DATA)
            second = sorter.sort(list(self.DATA))
            self.assertEqual(first, second)
        # explain 能说明每个未覆盖字符走了哪条路径
        fold = self.make_sorter("fold")
        sources = [e["source"] for e in fold.explain("aå文")]
        self.assertEqual(sources[0], "letters")
        self.assertEqual(sources[1], "fold->'a'")
        self.assertEqual(sources[2], "fold->codepoint")
        codepoint = self.make_sorter("codepoint")
        sources = [e["source"] for e in codepoint.explain("a文")]
        self.assertEqual(sources, ["letters", "codepoint"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
