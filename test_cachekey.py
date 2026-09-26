"""修复版缓存键的键等价性 / 可区分性回归测试。

覆盖：
- 规范化规则：参数名大小写折叠、字段排序、空值区分、默认值省略、
  不稳定字段剔除、集合确定化、元组=列表、类型保真。
- 空参数、单参数、嵌套结构、非 ASCII（NFC 等价）。
- 性质测试：等价变换同键；任意单叶变异产生不同键。
- ParamCache：端到端命中，以及摘要碰撞也不会合并不同请求。

运行：python3 -m unittest test_cachekey -v
"""

import copy
import random
import unicodedata
import unittest

import cachekey
from cachekey import ParamCache, canonicalize, digest

SAME_KEY = "语义相同必须命中同一键"
DIFF_KEY = "语义不同必须使用不同键"


class EquivalenceTests(unittest.TestCase):
    """性质一：语义相同 -> 同键。"""

    def test_empty_params(self):
        self.assertEqual(digest(None), digest({}))
        self.assertEqual(canonicalize(None), canonicalize({}))
        # 空参数剔除全部不稳定字段后仍是空参数
        self.assertEqual(
            digest({"timestamp": 1, "requestId": "x"}), digest({})
        )

    def test_single_param_order_and_key_case(self):
        self.assertEqual(
            digest({"q": "python"}), digest({"Q": "python"})
        )

    def test_key_order_independent(self):
        requests = [
            {"a": 1, "b": 2, "c": 3},
            {"c": 3, "b": 2, "a": 1},
            {"B": 2, "a": 1, "C": 3},
        ]
        keys = {digest(r) for r in requests}
        self.assertEqual(len(keys), 1, SAME_KEY)

    def test_nested_structure_order_independent(self):
        a = {"filter": {"city": "上海", "tag": "x", "page": 1}, "sort": "asc"}
        b = {"sort": "asc", "FILTER": {"PAGE": 1, "tag": "x", "city": "上海"}}
        self.assertEqual(digest(a), digest(b), SAME_KEY)

    def test_list_order_is_significant(self):
        self.assertNotEqual(
            digest({"ids": [1, 2, 3]}), digest({"ids": [3, 2, 1]}),
            "列表有序，顺序不同语义不同",
        )

    def test_tuple_equals_list(self):
        self.assertEqual(
            digest({"ids": (1, 2, 3)}), digest({"ids": [1, 2, 3]}),
            "HTTP 参数无元组类型，元组按列表处理",
        )

    def test_set_is_determinized(self):
        a = {"tags": {"b", "a", "c"}}
        for _ in range(20):
            shuffled = list(a["tags"])
            random.Random(0).shuffle(shuffled)
            self.assertEqual(
                digest({"tags": set(shuffled)}), digest(a),
                "集合无序，必须确定化为同一个键",
            )

    def test_volatile_fields_ignored_by_default(self):
        base = {"q": "x", "page": 9}
        variants = [
            dict(base, timestamp=1),
            dict(base, timestamp=2, requestId="abc"),
            dict(base, TS=3, trace_id="t", nonce="n"),
        ]
        keys = {digest(v) for v in variants}
        self.assertEqual(keys, {digest(base)}, SAME_KEY)

    def test_custom_volatile_keys(self):
        params = {"a": 1, "sig": "abc"}
        self.assertEqual(
            digest(params, volatile_keys={"sig"}), digest({"a": 1})
        )

    def test_default_values_omitted(self):
        defaults = {"page": 1, "lang": "en", "enabled": False}
        self.assertEqual(
            digest({"page": 1, "lang": "en"}, defaults=defaults),
            digest({}, defaults=defaults),
            "显式传入默认值 == 省略该参数",
        )
        self.assertEqual(
            digest({"PAGE": 1}, defaults=defaults),
            digest({}, defaults=defaults),
            "默认值省略在大小写折叠后判定",
        )
        self.assertNotEqual(
            digest({"page": 2}, defaults=defaults),
            digest({}, defaults=defaults),
            "非默认值必须区分",
        )
        self.assertNotEqual(
            digest({"enabled": False}), digest({}),
            "未声明默认值时 false 不允许省略",
        )

    def test_non_ascii_nfc_equivalence(self):
        nfc = unicodedata.normalize("NFC", "école  Köln 北京😀")
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd, "夹具检查：两种 Unicode 表示确实不同")
        self.assertEqual(
            digest({"city": nfd}), digest({"city": nfc}),
            "NFC 归一化后同一文本必须同键",
        )

    def test_idempotent_and_deterministic(self):
        params = {"z": [{"k": "v"}], "a": None, "m": {"y": 1, "x": 2}}
        self.assertEqual(canonicalize(params), canonicalize(params))
        self.assertEqual(digest(params), digest(copy.deepcopy(params)))


class DistinctnessTests(unittest.TestCase):
    """性质二：语义不同 -> 不同键。"""

    def test_four_empty_value_variants_are_distinct(self):
        requests = [
            {},
            {"a": None},
            {"a": ""},
            {"a": []},
            {"a": {}},
        ]
        keys = {digest(r) for r in requests}
        self.assertEqual(len(keys), len(requests), DIFF_KEY)

    def test_types_are_distinct(self):
        requests = [
            {"a": 1}, {"a": "1"}, {"a": True}, {"a": 1.0},
        ]
        keys = {digest(r) for r in requests}
        self.assertEqual(len(keys), len(requests), "1 / \"1\" / true / 1.0")

    def test_injection_like_values_cannot_merge(self):
        pairs = [
            ({"a": "b", "c": "d"}, {"a": "b&c=d"}),
            ({"a": "b", "c": "d"}, {"a": "b=c&", "d": "x"}),
            ({"a": {"b": 1}}, {"a.b": 1}),
            ({"a": ["1", "2"]}, {"a": "1", "a.1": "2"}),
        ]
        for left, right in pairs:
            self.assertNotEqual(digest(left), digest(right), DIFF_KEY)

    def test_single_leaf_mutations_are_distinct(self):
        base = {"page": 2, "filter": {"city": "x", "tag": "y"},
                "ids": [1, 2]}
        mutations = [
            {"page": 3, "filter": {"city": "x", "tag": "y"}, "ids": [1, 2]},
            {"page": 2, "filter": {"city": "z", "tag": "y"}, "ids": [1, 2]},
            {"page": 2, "filter": {"city": "x", "tag": "y"}, "ids": [1, 3]},
            {"page": 2, "filter": {"city": "x"}, "ids": [1, 2]},
            {"page": 2, "filter": {"city": "x", "tag": "y", "extra": 1},
             "ids": [1, 2]},
        ]
        for changed in mutations:
            self.assertNotEqual(digest(base), digest(changed), DIFF_KEY)

    def test_long_values_never_truncated(self):
        requests = [{"blob": "p" * 5000 + suffix}
                    for suffix in ("AAA", "BBB", "CCC")]
        keys = {digest(r) for r in requests}
        self.assertEqual(len(keys), 3, DIFF_KEY)
        for params in requests:
            self.assertEqual(len(digest(params)), len("ck2:") + 64)


class PropertyTests(unittest.TestCase):
    """随机结构的等价变换必须同键；单叶变异必须异键。"""

    SCALARS = [None, "", "v", "1", 1, 2, True, False, 3.5,
               "北京", unicodedata.normalize("NFD", "é")]

    def random_value(self, rng, depth=0):
        if depth >= 3:
            return rng.choice(self.SCALARS)
        kind = rng.choice(["scalar", "list", "map", "scalar"])
        if kind == "scalar":
            return rng.choice(self.SCALARS)
        if kind == "list":
            return [self.random_value(rng, depth + 1)
                    for _ in range(rng.randrange(3))]
        names = rng.sample(["k", "n", "市", "z"], rng.randrange(1, 4))
        return {name: self.random_value(rng, depth + 1) for name in names}

    def shuffled(self, value, rng):
        if isinstance(value, dict):
            items = list(value.items())
            rng.shuffle(items)
            return {k: self.shuffled(v, rng) for k, v in items}
        if isinstance(value, list):
            return [self.shuffled(v, rng) for v in value]
        return value

    def test_equivalence_transformations_share_key(self):
        rng = random.Random(20260926)
        for _ in range(200):
            params = self.random_value(rng)
            if not isinstance(params, dict):
                params = {"root": params}
            changed = self.shuffled(copy.deepcopy(params), rng)
            if rng.random() < 0.5:
                changed["Timestamp"] = rng.randrange(10**9)
                changed["requestID"] = "req-" + str(rng.random())
            self.assertEqual(
                digest(params), digest(changed),
                f"等价变换未命中同一键: {params!r} vs {changed!r}",
            )

    def mutate_one_leaf(self, value, rng):
        if isinstance(value, dict):
            clone = dict(value)
            name = rng.choice(list(clone))
            clone[name] = self.mutate_one_leaf(clone[name], rng)
            return clone
        if isinstance(value, list):
            clone = list(value)
            if clone:
                idx = rng.randrange(len(clone))
                clone[idx] = self.mutate_one_leaf(clone[idx], rng)
            else:
                clone.append("mut")
            return clone
        replacements = {None: "x", "": "x", "v": "w", "1": "2", 1: 99,
                        2: 3, True: False, False: True, 3.5: 3.6,
                        "北京": "上海"}
        return replacements.get(value, "mutated-" + str(value))

    def test_any_leaf_mutation_changes_key(self):
        rng = random.Random(98765432)
        for _ in range(200):
            params = self.random_value(rng)
            if not isinstance(params, dict):
                params = {"root": params}
            changed = self.mutate_one_leaf(copy.deepcopy(params), rng)
            self.assertNotEqual(
                digest(params), digest(changed),
                f"语义不同却同键: {params!r} vs {changed!r}",
            )


class ParamCacheTests(unittest.TestCase):
    def test_end_toend_hit_and_miss(self):
        cache = ParamCache()
        cache.set({"q": "py", "page": 1, "timestamp": 100}, "result-A")
        self.assertEqual(
            cache.get({"page": 1, "Q": "py", "timestamp": 999}), "result-A"
        )
        self.assertIsNone(cache.get({"q": "py", "page": 2}))
        self.assertEqual(len(cache), 1)

    def test_digest_collision_does_not_merge_requests(self):
        # 强制所有摘要桶 id 相同，模拟 SHA-256 碰撞：
        # 即使桶 id 相同，完整规范串不同也不能合并。
        class CollisionCache(ParamCache):
            def _resolve(self, params):
                _, canonical = super()._resolve(params)
                return "forced-collision", canonical

        cache = CollisionCache()
        cache.set({"gid": 1, "note": "x" * 100}, "one")
        cache.set({"gid": 2, "note": "y" * 100}, "two")
        cache.set({"gid": 2, "note": "y" * 100, "ts": 7}, "two-again")

        self.assertEqual(cache.entries_for({"gid": 1}), 2)
        self.assertEqual(cache.get({"gid": 1, "note": "x" * 100}), "one")
        self.assertEqual(cache.get({"gid": 2, "note": "y" * 100}), "two-again")
        self.assertIsNone(cache.get({"gid": 3, "note": "z" * 100}))
        self.assertEqual(len(cache), 2, DIFF_KEY)

    def test_ambiguous_folded_names_rejected(self):
        with self.assertRaises(ValueError):
            digest({"ID": 1, "id": 2})

    def test_unsupported_type_policy(self):
        with self.assertRaises(ValueError):
            digest({"a": object()})
        self.assertEqual(
            digest({"a": object(), "b": 1}, on_unsupported="drop"),
            digest({"b": 1}),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
