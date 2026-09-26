"""multisort 的稳定性、全序校验与混合类型策略测试（仅标准库 unittest）。"""

import random
import unittest

from multisort import (
    KeySpec,
    MixedTypeError,
    TotalOrderError,
    sort_multi,
    sort_multi_reference,
)


def make_rows(n, seed=1, with_nulls=True):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        rows.append({
            "idx": i,
            "dept": rng.choice(["eng", "ops", "sales", None] if with_nulls
                               else ["eng", "ops", "sales"]),
            "score": rng.choice([None, None, 1, 2, 3, 4, 5] if with_nulls
                                else [1, 2, 3, 4, 5]),
            "name": rng.choice(["amy", "bob", "cy", "dee"]),
        })
    return rows


def ids(rows):
    return [r["idx"] for r in rows]


class StabilityTest(unittest.TestCase):
    KEYS = [
        KeySpec("dept", nulls="first"),
        KeySpec("score", reverse=True, nulls="last"),
        KeySpec("name"),
    ]

    def test_stable_equal_keys_keep_original_order(self):
        rows = [{"idx": i, "k": "same"} for i in range(1000)]
        out = sort_multi(rows, [KeySpec("k")])
        self.assertEqual(ids(out), list(range(1000)))

    def test_matches_multipass_reference_fast_path(self):
        rows = make_rows(3000, seed=7)
        out = sort_multi(rows, self.KEYS)
        ref = sort_multi_reference(rows, self.KEYS)
        self.assertEqual(ids(out), ids(ref))

    def test_matches_multipass_reference_comparator_path(self):
        rows = make_rows(3000, seed=8)
        keys = self.KEYS + [KeySpec("idx", comparator=lambda a, b: (a > b) - (a < b))]
        out = sort_multi(rows, keys)
        ref = sort_multi_reference(rows, keys)
        self.assertEqual(ids(out), ids(ref))

    def test_randomized_many_seeds(self):
        for seed in range(20):
            rows = make_rows(300, seed=100 + seed)
            keys = [
                KeySpec("dept", reverse=seed % 2 == 0,
                        nulls="first" if seed % 3 else "last"),
                KeySpec("score", reverse=seed % 3 == 0, nulls="last"),
                KeySpec("name"),
            ]
            self.assertEqual(ids(sort_multi(rows, keys)),
                             ids(sort_multi_reference(rows, keys)),
                             msg="seed=%d" % seed)

    def test_stability_relative_order_preserved(self):
        rows = [{"idx": i, "k": i % 3} for i in range(300)]
        out = sort_multi(rows, [KeySpec("k")])
        for group in range(3):
            got = [r["idx"] for r in out if r["k"] == group]
            self.assertEqual(got, sorted(got))


class TotalOrderTest(unittest.TestCase):
    ROWS = [{"idx": i, "v": i % 3} for i in range(90)]

    def test_reflexivity_violation_detected(self):
        bad = lambda a, b: 1  # cmp(a,a)=1
        with self.assertRaises(TotalOrderError) as ctx:
            sort_multi(self.ROWS, [KeySpec("v", comparator=bad)])
        self.assertIn("自反性", str(ctx.exception))

    def test_antisymmetry_violation_detected(self):
        bad = lambda a, b: 0 if a == b else -1  # cmp(1,2)=cmp(2,1)=-1
        with self.assertRaises(TotalOrderError) as ctx:
            sort_multi(self.ROWS, [KeySpec("v", comparator=bad)])
        self.assertIn("反对称性", str(ctx.exception))

    def test_transitivity_violation_detected(self):
        # 石头剪刀布式循环比较：自反、反对称，但不传递
        def bad(a, b):
            if a == b:
                return 0
            return 1 if (a - b) % 3 == 1 else -1
        with self.assertRaises(TotalOrderError) as ctx:
            sort_multi(self.ROWS, [KeySpec("v", comparator=bad)])
        self.assertIn("传递性", str(ctx.exception))

    def test_valid_comparator_passes(self):
        by_len = lambda a, b: (len(a) > len(b)) - (len(a) < len(b))
        rows = [{"name": n} for n in ["ccc", "a", "bb", "dd", "e"]]
        out = sort_multi(rows, [KeySpec("name", comparator=by_len)])
        self.assertEqual([r["name"] for r in out], ["a", "e", "bb", "dd", "ccc"])


class MixedTypeTest(unittest.TestCase):
    ROWS = [{"idx": i, "v": v}
            for i, v in enumerate([2, "b", None, 1, "a", 10])]

    def test_reject_is_default(self):
        with self.assertRaises(MixedTypeError):
            sort_multi(self.ROWS, [KeySpec("v")])

    def test_group_strategy(self):
        out = sort_multi(self.ROWS, [KeySpec("v")], mixed="group")
        # number 组排在 str 组前（按类别名排序），组内各自有序，None 最后
        self.assertEqual([r["v"] for r in out], [1, 2, 10, "a", "b", None])

    def test_coerce_strategy_explicit_string(self):
        out = sort_multi(self.ROWS, [KeySpec("v")], mixed="coerce")
        # 显式声明统一转 str：'1'<'10'<'2'<'a'<'b'
        self.assertEqual([r["v"] for r in out], [1, 10, 2, "a", "b", None])

    def test_coerce_to_float(self):
        rows = [{"v": v} for v in ["3.5", 2, "1e1", 0.5]]
        out = sort_multi(rows, [KeySpec("v")], mixed="coerce", coerce_to=float)
        self.assertEqual([r["v"] for r in out], [0.5, 2, "3.5", "1e1"])

    def test_int_float_mixed_is_same_category(self):
        rows = [{"idx": i, "v": v} for i, v in enumerate([3, 2.5, 4, 1.5, 2])]
        out = sort_multi(rows, [KeySpec("v")])  # 默认 reject 也不应报错
        self.assertEqual([r["v"] for r in out], [1.5, 2, 2.5, 3, 4])
        ref = sort_multi_reference(rows, [KeySpec("v")])
        self.assertEqual(ids(out), ids(ref))


class EdgeCaseTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sort_multi([], [KeySpec("a")]), [])
        self.assertEqual(sort_multi([], []), [])

    def test_no_keys_returns_input_order(self):
        rows = [{"idx": i} for i in range(10)]
        self.assertEqual(ids(sort_multi(rows, [])), list(range(10)))

    def test_all_keys_equal(self):
        rows = [{"idx": i, "a": 1, "b": "x", "c": None} for i in range(500)]
        keys = [KeySpec("a"), KeySpec("b", reverse=True), KeySpec("c")]
        self.assertEqual(ids(sort_multi(rows, keys)), list(range(500)))

    def test_very_long_string_keys(self):
        rng = random.Random(3)
        base = "x" * 100_000
        rows = []
        for i in range(50):
            pos = rng.randrange(len(base))
            ch = chr(rng.randrange(ord("a"), ord("z") + 1))
            rows.append({"idx": i, "s": base[:pos] + ch + base[pos + 1:]})
        out = sort_multi(rows, [KeySpec("s")])
        self.assertEqual([r["s"] for r in out], sorted(r["s"] for r in rows))
        self.assertEqual(ids(out), ids(sort_multi_reference(rows, [KeySpec("s")])))

    def test_nulls_first_and_last(self):
        rows = [{"v": v} for v in [3, None, 1, None, 2]]
        first = sort_multi(rows, [KeySpec("v", nulls="first")])
        last = sort_multi(rows, [KeySpec("v", nulls="last")])
        self.assertEqual([r["v"] for r in first], [None, None, 1, 2, 3])
        self.assertEqual([r["v"] for r in last], [1, 2, 3, None, None])

    def test_reverse_does_not_move_nulls(self):
        rows = [{"v": v} for v in [3, None, 1, 2]]
        out = sort_multi(rows, [KeySpec("v", reverse=True, nulls="first")])
        self.assertEqual([r["v"] for r in out], [None, 3, 2, 1])

    def test_callable_and_attr_keys(self):
        class Rec:
            def __init__(self, a, b):
                self.a, self.b = a, b
        rows = [Rec(2, "y"), Rec(1, "z"), Rec(1, "a")]
        out = sort_multi(rows, [KeySpec("a"), KeySpec(lambda r: r.b)])
        self.assertEqual([(r.a, r.b) for r in out],
                         [(1, "a"), (1, "z"), (2, "y")])

    def test_dict_spec_form(self):
        rows = [{"v": 2}, {"v": 1}]
        out = sort_multi(rows, [{"key": "v", "reverse": True}])
        self.assertEqual([r["v"] for r in out], [2, 1])

    def test_input_not_mutated(self):
        rows = [{"v": 2}, {"v": 1}]
        sort_multi(rows, [KeySpec("v")])
        self.assertEqual([r["v"] for r in rows], [2, 1])


if __name__ == "__main__":
    unittest.main()
