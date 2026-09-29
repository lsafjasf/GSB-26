"""规则覆盖度分析的回归测试。

运行：python3 -m unittest test_coverage -v
"""

import unittest

import datasets
import refactored_decision
import rule_coverage
from rule_coverage import (
    analyze, count_hits, decide, find_dead_rules, infer_domains,
    render_report, strip_dead_rules,
)
from rule_engine import Engine, Rule

# 与 infer_domains 的输出一致（按 repr 排序）
DOMAINS = {"coupon": [False, True], "flagged": [False, True],
           "region": ["domestic", "overseas", "remote"],
           "tier": ["gold", "normal", "platinum", "silver"]}


def business_rules():
    return list(refactored_decision.RULES)


class HitCountTest(unittest.TestCase):
    """命中统计：每条规则在数据集上决定结果的次数。"""

    def test_grid_hit_counts(self):
        rules = business_rules()
        report = analyze(rules, datasets.grid_cases(), dataset_name="grid")
        self.assertEqual(report.total, datasets.GRID_SIZE)
        self.assertEqual(sum(report.hits.values()), datasets.GRID_SIZE)
        # 手工核算的三条规则命中数（与数据集定义直接对应）：
        # 规则 1: flagged ∧ tier∈{gold,platinum} ∧ days≥365
        #         = 2 tier * 3 region * 24 amount * 3 days * 2 coupon = 864
        self.assertEqual(report.hits[1], 864)
        # 规则 2: flagged 共 3456 例，扣除规则 1 的 864 例 = 2592
        self.assertEqual(report.hits[2], 2592)
        # 规则 15（默认）: 国内 normal 未命中规则 14 的输入
        #         = (24 无券 + 3 有券但 amount<200) * 6 days = 162
        self.assertEqual(report.hits[15], 162)

    def test_business_table_fully_covered_no_dead(self):
        report = analyze(business_rules(), datasets.grid_cases(),
                         domains=DOMAINS, dataset_name="grid")
        self.assertEqual(report.uncovered, [])   # 15 条规则均被网格触达
        self.assertEqual(report.dead, [])        # 无死规则
        self.assertFalse(report.default_shadowed)
        self.assertEqual(report.diff_mismatches, [])

    def test_hit_count_order_independent(self):
        cases = list(datasets.grid_cases())
        hits_a, _ = count_hits(business_rules(), cases)
        hits_b, _ = count_hits(business_rules(), list(reversed(cases)))
        self.assertEqual(hits_a, hits_b)


class MatcherConsistencyTest(unittest.TestCase):
    """免校验匹配器与 Engine 语义一致（对拍基准可信）。"""

    def test_decide_matches_engine(self):
        rules = business_rules()
        for case in datasets.grid_cases():
            self.assertEqual(decide(rules, case),
                             refactored_decision.decide(case))


class DeadRuleTest(unittest.TestCase):
    """死规则检测：自相矛盾 / 单条遮蔽 / 并集遮蔽 / 取值域。"""

    @staticmethod
    def default_rule():
        return Rule(None, "standard", priority=0, name="默认")

    def dead_ids(self, rules, domains=None):
        dead, _ = find_dead_rules(rules, domains)
        return [r.rid for r, _ in dead]

    def setUp(self):
        # find_dead_rules 依赖 rid，模拟 Engine 的编号
        self._n = [0]

    def numbered(self, rules):
        for i, r in enumerate(rules, start=1):
            r.rid = i
        return rules

    def test_contradictory(self):
        rules = self.numbered([
            Rule({"amount": {"gte": 1000, "lt": 500}}, "vip_fast", 10),
            self.default_rule(),
        ])
        self.assertEqual(self.dead_ids(rules), [1])

    def test_shadowed_by_single_rule(self):
        rules = self.numbered([
            Rule({"tier": {"eq": "gold"}}, "discount_10", 10),
            Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                 "discount_20", 5),
            self.default_rule(),
        ])
        self.assertEqual(self.dead_ids(rules), [2])

    def test_shadowed_by_union_of_ranges(self):
        # 单条都盖不住 amount>=0，但 [0,500) ∪ [500,+inf) 合力遮蔽
        rules = self.numbered([
            Rule({"amount": {"gte": 500}}, "vip_fast", 10),
            Rule({"amount": {"lt": 500}}, "discount_10", 9),
            Rule({"amount": {"gte": 0}}, "standard", 5),
            self.default_rule(),
        ])
        self.assertEqual(self.dead_ids(rules), [3])

    def test_shadowed_by_union_of_sets(self):
        rules = self.numbered([
            Rule({"tier": {"eq": "gold"}}, "discount_10", 10),
            Rule({"tier": {"eq": "silver"}}, "standard", 9),
            Rule({"tier": {"in": ["gold", "silver"]}}, "vip_fast", 5),
            self.default_rule(),
        ])
        self.assertEqual(self.dead_ids(rules), [3])

    def test_union_shadow_needs_domain_for_unconstrained_field(self):
        # coupon=True 被 tier=gold∧coupon 与 tier=silver∧coupon 合力遮蔽，
        # 但前提是你知道 tier 只有 gold/silver 两种取值。
        def build():
            return self.numbered([
                Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                     "discount_20", 10),
                Rule({"tier": {"eq": "silver"}, "coupon": {"eq": True}},
                     "discount_10", 9),
                Rule({"coupon": {"eq": True}}, "vip_fast", 5),
                self.default_rule(),
            ])
        # 无取值域：保守，不误报
        self.assertEqual(self.dead_ids(build()), [])
        # 有取值域：精确判定为死规则
        self.assertEqual(self.dead_ids(build(), {"tier": ["gold", "silver"]}),
                         [3])
        # 取值域更宽（还有 normal）：不遮蔽
        self.assertEqual(
            self.dead_ids(build(), {"tier": ["gold", "silver", "normal"]}),
            [])

    def test_partially_shadowed_is_not_dead(self):
        # 规则 2 只有 coupon=True 的一半被规则 1 盖住，coupon=False 仍可达
        rules = self.numbered([
            Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                 "discount_20", 10),
            Rule({"tier": {"eq": "gold"}}, "discount_10", 5),
            self.default_rule(),
        ])
        self.assertEqual(self.dead_ids(rules), [])

    def test_default_rule_shadowed(self):
        rules = self.numbered([
            Rule({"tier": {"eq": "gold"}}, "discount_10", 10),
            Rule({"tier": {"eq": "silver"}}, "standard", 9),
            self.default_rule(),
        ])
        _, shadowed = find_dead_rules(rules, {"tier": ["gold", "silver"]})
        self.assertTrue(shadowed)
        _, shadowed = find_dead_rules(rules)  # 无取值域 -> 保守不判定
        self.assertFalse(shadowed)


class DeleteDeadRulesDifferentialTest(unittest.TestCase):
    """删除死规则后行为不变（对拍）。"""

    def table_with_dead_rules(self):
        return [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10,
                 name="黄金九折"),
            Rule({"tier": {"eq": "gold"}, "coupon": {"eq": True}},
                 "discount_20", priority=5, name="被遮蔽的死规则"),
            Rule({"amount": {"gte": 100, "lt": 50}}, "vip_fast", priority=8,
                 name="自相矛盾的死规则"),
            Rule({"tier": {"eq": "silver"}}, "standard", priority=7,
                 name="白银标准"),
            Rule(None, "standard", priority=0, name="默认"),
        ]

    def test_delete_dead_rules_behavior_unchanged(self):
        rules = self.table_with_dead_rules()
        cases = list(datasets.grid_cases()) + list(datasets.fuzz_cases())
        report = analyze(rules, cases, domains=DOMAINS,
                         dataset_name="grid+fuzz")
        # 恰好识别出规则 2、3 为死规则
        self.assertEqual([r.rid for r, _ in report.dead], [2, 3])
        # analyze 内部已完成对拍：原表 vs 清理后 Engine 逐例一致
        self.assertEqual(report.diff_total, len(cases))
        self.assertEqual(report.diff_mismatches, [])
        # 清理后的表能通过 Engine 加载校验，且逐例一致（显式再对拍一次）
        cleaned = strip_dead_rules(rules, report.dead)
        engine = Engine(cleaned)
        for case in cases:
            self.assertEqual(decide(rules, case), engine.decide(case))

    def test_cleaned_table_loads_clean(self):
        rules = self.table_with_dead_rules()
        dead, _ = find_dead_rules(self.numbered(rules))
        Engine(strip_dead_rules(rules, dead))  # 不抛 RuleError 即通过

    @staticmethod
    def numbered(rules):
        for i, r in enumerate(rules, start=1):
            r.rid = i
        return rules


class UncoveredBranchTest(unittest.TestCase):
    """未被任何用例覆盖的分支清单。"""

    def test_uncovered_branch_listed(self):
        rules = [
            Rule({"tier": {"eq": "gold"}}, "discount_10", priority=10,
                 name="黄金九折"),
            Rule({"tier": {"eq": "silver"}}, "standard", priority=9,
                 name="白银标准"),
            Rule(None, "standard", priority=0, name="默认"),
        ]
        cases = [{"tier": "silver", "region": "domestic", "amount": 1,
                  "account_days": 0, "coupon": False, "flagged": False},
                 {"tier": "normal", "region": "domestic", "amount": 1,
                  "account_days": 0, "coupon": False, "flagged": False}]
        report = analyze(rules, cases, dataset_name="2 例")
        # 规则 1（gold）从未被触达；默认规则被 normal 用例触达
        self.assertEqual([r.rid for r in report.uncovered], [1])
        # 但它不是死规则（数据里没 gold 而已）
        self.assertEqual(report.dead, [])


class ReportReproducibilityTest(unittest.TestCase):
    """分析结论可复算：同一输入，报告逐字节一致。"""

    def test_report_deterministic(self):
        cases = list(datasets.grid_cases())
        text_a = render_report(analyze(business_rules(), cases,
                                       domains=DOMAINS, dataset_name="grid"))
        text_b = render_report(analyze(business_rules(), cases,
                                       domains=DOMAINS, dataset_name="grid"))
        self.assertEqual(text_a, text_b)

    def test_report_independent_of_case_order(self):
        cases = list(datasets.grid_cases())
        text_a = render_report(analyze(business_rules(), cases,
                                       domains=DOMAINS, dataset_name="grid"))
        text_b = render_report(analyze(business_rules(),
                                       list(reversed(cases)),
                                       domains=DOMAINS, dataset_name="grid"))
        self.assertEqual(text_a, text_b)

    def test_infer_domains_deterministic(self):
        cases = list(datasets.grid_cases())
        self.assertEqual(infer_domains(cases),
                         infer_domains(list(reversed(cases))))
        self.assertEqual(infer_domains(cases), DOMAINS)


if __name__ == "__main__":
    unittest.main()
