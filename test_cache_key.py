"""缓存键规范化的回归测试。

运行：python3 test_cache_key.py  （或 python3 -m unittest test_cache_key -v）

结构：
- BuggyReproductionTest：稳定复现修复前的四类现网问题。
- NormalizationRulesTest：固化规范化规则（排序/大小写/空值/默认值）。
- KeyEquivalenceTest：修复后的两条核心性质。
- EdgeCaseTest：空参数、单参数、嵌套结构、非 ASCII。
"""

import unittest

import cache_key
import cache_key_buggy


class BuggyReproductionTest(unittest.TestCase):
    """复现修复前的四类缺陷（这些断言描述的是"缺陷行为"）。"""

    def test_bug1_param_order_causes_miss(self):
        a = {"q": "phone", "page": 1}
        b = {"page": 1, "q": "phone"}  # 仅顺序不同，语义相同
        self.assertNotEqual(
            cache_key_buggy.make_key(a), cache_key_buggy.make_key(b),
            "缺陷1：参数顺序不同应（错误地）产生不同键",
        )

    def test_bug2_delimiter_injection_causes_pollution(self):
        # 语义不同但拼接文本相同
        a = {"q": "x&page=1"}            # 单参数，值里含 & 和 =
        b = {"q": "x", "page": 1}        # 两个参数
        self.assertEqual(
            cache_key_buggy.make_key(a), cache_key_buggy.make_key(b),
            "缺陷2：不转义导致语义不同的请求共享键",
        )

    def test_bug3_truncation_causes_collision(self):
        prefix = "p" * 100
        a = {"q": prefix + "_A"}
        b = {"q": prefix + "_B"}  # 前 64 字符与 a 完全相同
        self.assertEqual(
            cache_key_buggy.make_key(a), cache_key_buggy.make_key(b),
            "缺陷3：截断导致超长参数碰撞",
        )

    def test_bug4_volatile_fields_kill_hit_rate(self):
        a = {"q": "phone", "timestamp": 1727400000, "request_id": "r-1"}
        b = {"q": "phone", "timestamp": 1727400001, "request_id": "r-2"}
        self.assertNotEqual(
            cache_key_buggy.make_key(a), cache_key_buggy.make_key(b),
            "缺陷4：不稳定字段参与键，同一语义请求无法命中",
        )


class NormalizationRulesTest(unittest.TestCase):
    """固化规范化规则，规则变更必须显式修改本测试与文档。"""

    def test_rule_sorting(self):
        self.assertEqual(
            cache_key.make_key({"b": 2, "a": 1}),
            cache_key.make_key({"a": 1, "b": 2}),
        )

    def test_rule_key_case_insensitive_value_case_sensitive(self):
        self.assertEqual(
            cache_key.make_key({"Name": "x"}), cache_key.make_key({"name": "x"})
        )
        self.assertNotEqual(
            cache_key.make_key({"q": "ABC"}), cache_key.make_key({"q": "abc"})
        )

    def test_rule_empty_values_omitted(self):
        base = cache_key.make_key({"q": "x"})
        for empty in (None, "", [], {}):
            self.assertEqual(cache_key.make_key({"q": "x", "extra": empty}), base)

    def test_rule_defaults_omitted(self):
        defaults = {"page": 1, "filter": {"sort": "asc"}}
        self.assertEqual(
            cache_key.make_key({"q": "x"}, defaults),
            cache_key.make_key({"q": "x", "page": 1}, defaults),
        )
        self.assertEqual(
            cache_key.make_key({"q": "x"}, defaults),
            cache_key.make_key({"q": "x", "filter": {"sort": "asc"}}, defaults),
        )
        self.assertNotEqual(
            cache_key.make_key({"q": "x", "page": 2}, defaults),
            cache_key.make_key({"q": "x"}, defaults),
        )

    def test_rule_volatile_fields_removed(self):
        a = {"q": "x", "timestamp": 1, "request_id": "r1", "nonce": "n1"}
        b = {"q": "x", "timestamp": 2, "request_id": "r2", "nonce": "n2"}
        self.assertEqual(cache_key.make_key(a), cache_key.make_key(b))

    def test_rule_type_normalization(self):
        self.assertEqual(
            cache_key.make_key({"page": 1}), cache_key.make_key({"page": "1"})
        )
        self.assertEqual(
            cache_key.make_key({"page": 1.0}), cache_key.make_key({"page": 1})
        )
        self.assertEqual(
            cache_key.make_key({"flag": True}),
            cache_key.make_key({"flag": "true"}),
        )


class KeyEquivalenceTest(unittest.TestCase):
    """修复后的两条核心性质。"""

    def test_semantic_equal_same_key(self):
        a = {"q": "phone", "page": 1, "timestamp": 1727400000}
        b = {"timestamp": 1727409999, "page": "1", "q": "phone", "tag": []}
        self.assertEqual(cache_key.make_key(a), cache_key.make_key(b))

    def test_semantic_diff_different_key(self):
        pairs = [
            ({"q": "x&page=1"}, {"q": "x", "page": 1}),       # 文本相近
            ({"q": "phone"}, {"q": "phone", "page": 2}),
            ({"q": "ABC"}, {"q": "abc"}),
            ({"ids": [1, 2]}, {"ids": [2, 1]}),               # list 顺序是语义
        ]
        for a, b in pairs:
            self.assertNotEqual(cache_key.make_key(a), cache_key.make_key(b))

    def test_long_params_no_truncation_collision(self):
        prefix = "p" * 500
        a = {"q": prefix + "_A"}
        b = {"q": prefix + "_B"}
        ka, kb = cache_key.make_key(a), cache_key.make_key(b)
        self.assertNotEqual(ka, kb)
        self.assertTrue(ka.startswith("sha256:"))
        # 同一语义的长参数必须稳定命中
        self.assertEqual(ka, cache_key.make_key({"q": prefix + "_A"}))

    def test_no_ambiguity_between_short_and_long_keys(self):
        long_key = cache_key.make_key({"q": "p" * 500})
        short_key = cache_key.make_key({"q": "x"})
        self.assertNotEqual(long_key, short_key)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_params(self):
        self.assertEqual(cache_key.make_key({}), cache_key.make_key(None))
        self.assertEqual(cache_key.make_key({}), cache_key.make_key({"a": None}))

    def test_single_param(self):
        self.assertEqual(
            cache_key.make_key({"q": "x"}), cache_key.make_key({"q": "x"})
        )
        self.assertNotEqual(
            cache_key.make_key({"q": "x"}), cache_key.make_key({"q": "y"})
        )

    def test_nested_params(self):
        a = {"filter": {"color": "red", "size": {"w": 10, "h": 20}}}
        b = {"filter": {"size": {"h": 20, "w": 10}, "color": "red"}}
        self.assertEqual(cache_key.make_key(a), cache_key.make_key(b))
        c = {"filter": {"color": "red", "size": {"w": 10, "h": 21}}}
        self.assertNotEqual(cache_key.make_key(a), cache_key.make_key(c))

    def test_nested_volatile_and_empty(self):
        a = {"filter": {"sort": "asc", "ts": 1, "note": ""}}
        b = {"filter": {"sort": "asc", "ts": 2}}
        self.assertEqual(cache_key.make_key(a), cache_key.make_key(b))

    def test_non_ascii_params(self):
        a = {"q": "手机", "城市": "上海"}
        b = {"城市": "上海", "q": "手机"}
        self.assertEqual(cache_key.make_key(a), cache_key.make_key(b))
        self.assertNotEqual(
            cache_key.make_key({"q": "手机"}), cache_key.make_key({"q": "手机壳"})
        )
        # 非 ASCII 长参数同样走哈希路径且不碰撞
        prefix = "参数" * 200
        self.assertNotEqual(
            cache_key.make_key({"q": prefix + "甲"}),
            cache_key.make_key({"q": prefix + "乙"}),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
