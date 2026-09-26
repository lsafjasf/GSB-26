"""multisort 的单元测试：稳定性、全序校验、混合类型策略与边界情形。

运行：python3 -m unittest test_multisort -v
"""

import random
import unittest
from functools import cmp_to_key

from multisort import (
    KeySpec,
    MixedTypeError,
    TotalOrderError,
    make_comparator,
    sort_multi,
    validate_total_order,
)


def reference_multipass(records, specs):
    """独立的参考实现：多次稳定排序（每趟 = 稳定划分空值 + 稳定排序）。"""
    result = list(records)
    for spec in reversed(specs):
        extract = spec.key if callable(spec.key) else (
            (lambda r, k=spec.key: r[k]) if isinstance(spec.key, int)
            else (lambda r, k=spec.key: r[k] if isinstance(r, dict) else getattr(r, k))
        )
        nulls = [r for r in result if extract(r) is None]
        non_null = [r for r in result if extract(r) is not None]
        non_null.sort(key=extract, reverse=spec.reverse)
        result = nulls + non_null if spec.nulls == "first" else non_null + nulls
    return result


def make_random_records(n, seed=42):
    rng = random.Random(seed)
    records = []
    for i in range(n):
        records.append({
            "a": rng.choice([None, 1, 2, 3, 4, 5]),
            "b": rng.choice([None, 0.5, 1.5, 2, 3]),  # float/int 混合
            "c": rng.choice(["x", "y", "zz", None]),
            "idx": i,  # 用于验证稳定性
        })
    return records


class TestStability(unittest.TestCase):
    SPECS = [
        KeySpec("a", reverse=False, nulls="first"),
        KeySpec("b", reverse=True, nulls="last"),
        KeySpec("c", reverse=True, nulls="first"),
    ]

    def test_empty(self):
        self.assertEqual(sort_multi([], self.SPECS), [])
        self.assertEqual(sort_multi([], ["a"]), [])

    def test_all_keys_equal_keeps_original_order(self):
        records = [{"a": 1, "idx": i} for i in range(1000)]
        out = sort_multi(records, [KeySpec("a")])
        self.assertEqual([r["idx"] for r in out], list(range(1000)))

    def test_single_pass_matches_multipass(self):
        """单趟比较器（cmp_to_key）结果必须与多次稳定排序一致。"""
        records = make_random_records(5000)
        cmp_records = make_comparator(self.SPECS)
        single_pass = sorted(records, key=cmp_to_key(cmp_records))
        multi_pass = reference_multipass(records, self.SPECS)
        lib_result = sort_multi(records, self.SPECS)
        self.assertEqual(single_pass, multi_pass)
        self.assertEqual(lib_result, multi_pass)

    def test_stability_preserves_relative_order(self):
        records = make_random_records(5000)
        out = sort_multi(records, self.SPECS)
        # 按键完全相同的记录，idx 必须递增（保持原始相对顺序）
        for prev, cur in zip(out, out[1:]):
            ka = tuple(prev[k] for k in ("a", "b", "c"))
            kb = tuple(cur[k] for k in ("a", "b", "c"))
            if ka == kb:
                self.assertLess(prev["idx"], cur["idx"])

    def test_reverse_and_null_positions(self):
        records = [{"v": v} for v in [3, None, 1, None, 2]]
        self.assertEqual(
            [r["v"] for r in sort_multi(records, [KeySpec("v", nulls="first")])],
            [None, None, 1, 2, 3],
        )
        self.assertEqual(
            [r["v"] for r in sort_multi(records, [KeySpec("v", nulls="last", reverse=True)])],
            [3, 2, 1, None, None],  # 空值位置不受 reverse 影响
        )


class TestTotalOrderValidation(unittest.TestCase):
    def setUp(self):
        self.items = list(range(50))

    def test_good_comparator_passes(self):
        validate_total_order(lambda a, b: (a > b) - (a < b), self.items)

    def test_reflexivity_violation_detected(self):
        with self.assertRaises(TotalOrderError) as ctx:
            validate_total_order(lambda a, b: 1, self.items)  # cmp(x,x)=1
        self.assertIn("自反性", str(ctx.exception))

    def test_antisymmetry_violation_detected(self):
        # cmp(a,b) 与 cmp(b,a) 同号
        with self.assertRaises(TotalOrderError) as ctx:
            validate_total_order(lambda a, b: 1 if a != b else 0, self.items)
        self.assertIn("反对称性", str(ctx.exception))

    def test_transitivity_violation_detected(self):
        # 石头剪刀布式循环比较：满足自反与反对称，但 0<1<2<0 破坏传递性
        beats = {(0, 1), (1, 2), (2, 0)}

        def rock_paper_scissors(a, b):
            if a == b:
                return 0
            return -1 if (a, b) in beats else 1

        with self.assertRaises(TotalOrderError) as ctx:
            validate_total_order(rock_paper_scissors, [0, 1, 2])
        self.assertIn("传递性", str(ctx.exception))

    def test_sort_multi_rejects_bad_comparator(self):
        records = [{"v": i} for i in range(100)]
        bad = KeySpec("v", cmp=lambda a, b: 1 if a != b else 0)
        with self.assertRaises(TotalOrderError):
            sort_multi(records, [bad])
        # validate=False 时跳过校验（调用方自负其责）
        sort_multi(records, [bad], validate=False)

    def test_random_fuzz_valid_comparator(self):
        rng = random.Random(7)
        for _ in range(20):
            records = [
                (rng.choice([None, 1, 2, 3]), rng.choice([None, "a", "b"]))
                for _ in range(200)
            ]
            specs = [
                KeySpec(0, reverse=rng.random() < 0.5,
                        nulls=rng.choice(["first", "last"])),
                KeySpec(1, reverse=rng.random() < 0.5,
                        nulls=rng.choice(["first", "last"])),
            ]
            out = sort_multi(records, specs)
            self.assertEqual(out, reference_multipass(records, specs))


class TestMixedTypes(unittest.TestCase):
    RECORDS = [{"v": v, "idx": i} for i, v in enumerate([3, "a", 1.5, "b", 2])]

    def test_reject_is_default(self):
        with self.assertRaises(MixedTypeError):
            sort_multi(self.RECORDS, ["v"])

    def test_group_strategy(self):
        out = sort_multi(self.RECORDS, [KeySpec("v", mixed="group")])
        # 数值组在前（数值内排序），字符串组在后（字符串内排序）
        self.assertEqual([r["v"] for r in out], [1.5, 2, 3, "a", "b"])

    def test_coerce_strategy(self):
        records = [{"v": v} for v in ["10", 2, "3", 1.5]]
        out = sort_multi(records, [KeySpec("v", mixed="coerce", coerce=float)])
        self.assertEqual([r["v"] for r in out], [1.5, 2, "3", "10"])

    def test_coerce_must_unify(self):
        with self.assertRaises(MixedTypeError):
            sort_multi(self.RECORDS, [KeySpec("v", mixed="coerce", coerce=lambda x: x)])

    def test_float_int_mix_is_numeric_not_mixed(self):
        records = [{"v": v} for v in [3, 1.5, 2, True, 0]]
        out = sort_multi(records, ["v"])  # 默认 reject 也不应报错
        self.assertEqual([r["v"] for r in out], [0, True, 1.5, 2, 3])

    def test_unsupported_type_raises(self):
        records = [{"v": v} for v in [object(), object()]]
        with self.assertRaises(MixedTypeError):
            sort_multi(records, ["v"])


class TestCustomComparator(unittest.TestCase):
    def test_custom_cmp(self):
        records = [{"v": v} for v in [-3, 1, -2, 4]]
        by_abs = KeySpec("v", cmp=lambda a, b: (abs(a) > abs(b)) - (abs(a) < abs(b)))
        out = sort_multi(records, [by_abs])
        self.assertEqual([r["v"] for r in out], [1, -2, -3, 4])

    def test_custom_cmp_path_matches_multipass_semantics(self):
        # 自定义 cmp 走单趟路径；与等价的内置多次排序结果一致
        records = make_random_records(2000)
        cmp_len = KeySpec("c", cmp=lambda a, b: (len(a) > len(b)) - (len(a) < len(b)),
                          nulls="first")
        custom = sort_multi(records, [cmp_len])
        builtin = sort_multi(records, [KeySpec(lambda r: len(r["c"]) if r["c"] is not None else None,
                                               nulls="first")])
        self.assertEqual(custom, builtin)


class TestEdgeCases(unittest.TestCase):
    def test_very_long_string_keys(self):
        base = "x" * 100_000
        records = [{"s": base + suffix, "idx": i}
                   for i, suffix in enumerate(["c", "a", "b", "a"])]
        out = sort_multi(records, ["s"])
        self.assertEqual([r["idx"] for r in out], [1, 3, 2, 0])  # "a" 两条保持稳定

    def test_tuple_records_with_index_keys(self):
        records = [(2, "b"), (1, "c"), (2, "a"), (1, "b")]
        out = sort_multi(records, [KeySpec(0), KeySpec(1, reverse=True)])
        self.assertEqual(out, [(1, "c"), (1, "b"), (2, "b"), (2, "a")])

    def test_no_keys_returns_copy(self):
        records = [{"a": 1}]
        out = sort_multi(records, [])
        self.assertEqual(out, records)
        self.assertIsNot(out, records)

    def test_input_not_mutated(self):
        records = make_random_records(100)
        snapshot = list(records)
        sort_multi(records, self_specs())
        self.assertEqual(records, snapshot)

    def test_invalid_spec_rejected(self):
        with self.assertRaises(ValueError):
            KeySpec("a", nulls="middle")
        with self.assertRaises(ValueError):
            KeySpec("a", mixed="coerce")  # 缺少 coerce


def self_specs():
    return [KeySpec("a", nulls="first"), KeySpec("b", reverse=True)]


if __name__ == "__main__":
    unittest.main()
