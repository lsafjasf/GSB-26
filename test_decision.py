"""回归测试：差分测试 + 顺序无关性 + 规则表校验（冲突/不可达）用例。

运行：python3 -m unittest test_decision -v
"""

import itertools
import random
import unittest

import legacy_decision
import refactored_decision
from datasets import fuzz_cases, grid_cases
from rule_engine import Engine, Rule, RuleError


class DifferentialTest(unittest.TestCase):
    """重构前后逐例一致。"""

    def test_exhaustive_grid(self):
        mismatches = []
        for case in grid_cases():
            old = legacy_decision.decide(case)
            new = refactored_decision.decide(case)
            if old != new:
                mismatches.append((case, old, new))
        self.assertEqual(mismatches, [])

    def test_random_fuzz(self):
        for case in fuzz_cases():
            self.assertEqual(legacy_decision.decide(case),
                             refactored_decision.decide(case),
                             msg="case=%r" % case)

    def test_all_results_covered(self):
        """网格确实触达全部 6 种判定结果（含默认分支 standard）。"""
        results = {refactored_decision.decide(c) for c in grid_cases()}
        self.assertEqual(results, {"reject", "manual_review", "vip_fast",
                                   "discount_20", "discount_10", "standard"})


class OrderIndependenceTest(unittest.TestCase):
    """行为只由显式优先级决定：打乱声明顺序后结果不变。"""

    def test_shuffled_declaration_order(self):
        cases = list(itertools.islice(grid_cases(), 0, None, 7))  # 抽样 1975 例
        expected = [refactored_decision.decide(c) for c in cases]
        rng = random.Random(42)
        for _ in range(5):
            shuffled = list(refactored_decision.RULES)
            rng.shuffle(shuffled)
            engine = Engine(shuffled)
            for case, want in zip(cases, expected):
                self.assertEqual(engine.decide(case), want,
                                 msg="case=%r" % case)

    def test_reverse_declaration_order(self):
        cases = list(itertools.islice(grid_cases(), 0, None, 11))
        engine = Engine(list(reversed(refactored_decision.RULES)))
        for case in cases:
            self.assertEqual(engine.decide(case),
                             legacy_decision.decide(case))


class RuleValidationTest(unittest.TestCase):
    """规则表加载时的冲突 / 不可达 / 默认规则检测。"""

    @staticmethod
    def default_rule():
        return Rule(None, "standard", priority=0, name="默认")

    def test_business_rules_load_clean(self):
        Engine(list(refactored_decision.RULES))  # 不抛异常即通过

    def test_conflict_same_priority_overlap(self):
        rules = [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10),
            Rule({"tier": {"in": ["gold", "silver"]}, "coupon": {"eq": True}},
                 "discount_20", priority=10),
            self.default_rule(),
        ]
        with self.assertRaises(RuleError) as ctx:
            Engine(rules)
        msg = str(ctx.exception)
        self.assertIn("规则 1", msg)
        self.assertIn("规则 2", msg)
        self.assertIn("冲突", msg)

    def test_no_conflict_when_priority_differs(self):
        # 条件重叠、结果不同，但优先级不同 -> 合法（高优先级胜出）
        rules = [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10),
            Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                 "discount_20", priority=20),
            self.default_rule(),
        ]
        Engine(rules)

    def test_no_conflict_when_disjoint(self):
        # 优先级相同、结果不同，但条件不重叠 -> 合法
        rules = [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10),
            Rule({"tier": {"eq": "silver"}}, "discount_20", priority=10),
            self.default_rule(),
        ]
        Engine(rules)

    def test_unreachable_covered_by_higher_priority(self):
        rules = [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10),
            Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                 "discount_20", priority=5),   # 被规则 1 完全覆盖且优先级更低
            self.default_rule(),
        ]
        with self.assertRaises(RuleError) as ctx:
            Engine(rules)
        msg = str(ctx.exception)
        self.assertIn("规则 2", msg)
        self.assertIn("不可达", msg)
        self.assertIn("规则 1", msg)

    def test_unreachable_contradictory_condition(self):
        rules = [
            Rule({"amount": {"gte": 1000, "lt": 500}}, "vip_fast", priority=10),
            self.default_rule(),
        ]
        with self.assertRaises(RuleError) as ctx:
            Engine(rules)
        self.assertIn("规则 1", str(ctx.exception))
        self.assertIn("不可达", str(ctx.exception))

    def test_missing_default_rule(self):
        with self.assertRaises(RuleError) as ctx:
            Engine([Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10)])
        self.assertIn("默认", str(ctx.exception))

    def test_duplicate_default_rule(self):
        with self.assertRaises(RuleError) as ctx:
            Engine([self.default_rule(), self.default_rule()])
        self.assertIn("规则 1", str(ctx.exception))
        self.assertIn("规则 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
