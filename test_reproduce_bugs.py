"""四类现网缺陷的稳定复现用例（针对旧版 cachekey_buggy.make_cache_key）。

运行：python3 -m unittest test_reproduce_bugs -v
每个用例同时断言：旧实现确实有缺陷；修复版 digest 已无该缺陷。
"""

import unittest

import cachekey
from cachekey_buggy import make_cache_key as old_key


class ReproduceBugs(unittest.TestCase):

    def test_1_param_order_changes_key(self):
        a = {"category": "books", "page": 2}
        b = {"page": 2, "category": "books"}

        # 旧实现：顺序变了键就变 -> 本应命中却未命中
        self.assertNotEqual(old_key(a), old_key(b))
        # 修复版：顺序无关
        self.assertEqual(cachekey.digest(a), cachekey.digest(b))

    def test_2_semantically_different_requests_collide(self):
        a = {"a": "b", "c": "d"}        # 参数 a=b, c=d
        b = {"a": "b&c=d"}              # 参数 a 的值就是 "b&c=d"

        # 旧实现：无分隔/转义，文本相同 -> 语义不同却合并
        self.assertEqual(old_key(a), old_key(b))
        # 修复版：canonical JSON 结构保真，键不同
        self.assertNotEqual(cachekey.digest(a), cachekey.digest(b))

        # 额外的类型碰撞：字符串 "1" 与整数 1
        self.assertEqual(old_key({"n": "1"}), old_key({"n": 1}))
        self.assertNotEqual(
            cachekey.digest({"n": "1"}), cachekey.digest({"n": 1})
        )

    def test_3_truncation_collision_on_long_keys(self):
        common_prefix = "x" * 60
        a = {"note": common_prefix + "AAA", "gid": 1}
        b = {"note": common_prefix + "BBB", "gid": 2}

        self.assertGreater(len("note=" + a["note"] + "&gid=1"), 64)
        # 旧实现：超过 64 字符截断 -> 不同请求同键
        self.assertEqual(old_key(a), old_key(b))
        # 修复版：完整规范化串 + SHA-256，绝不截断碰撞
        self.assertNotEqual(cachekey.digest(a), cachekey.digest(b))

    def test_4_volatile_fields_destroy_hit_rate(self):
        base = {"q": "python", "page": 3}
        at_t1 = dict(base, timestamp=1_700_000_000, requestId="req-001")
        at_t2 = dict(base, timestamp=1_700_000_001, requestId="req-002")

        # 旧实现：时间戳/请求标识参与键 -> 同义请求永远 miss
        self.assertNotEqual(old_key(at_t1), old_key(at_t2))
        # 修复版：不稳定字段默认剔除，稳定命中
        self.assertEqual(cachekey.digest(at_t1), cachekey.digest(at_t2))
        self.assertEqual(cachekey.digest(at_t1), cachekey.digest(base))


if __name__ == "__main__":
    unittest.main(verbosity=2)
