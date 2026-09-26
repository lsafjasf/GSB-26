"""顺序敏感性验证。

1. 随机打乱规则声明顺序 -> 行为完全不变（顺序只由 priority 决定）；
2. 交换两组重叠规则的优先级 -> 行为按预期改变，
   证明顺序敏感逻辑已被显式编码进 priority，而非隐式依赖声明顺序。
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rule_engine import Engine, Rule
from rules import RULES


def decide_with(engine, level, amount, weight, region, fragile):
    ctx = {"level": level, "amount": amount, "weight": weight,
           "region": region, "fragile": fragile}
    method, fee = engine.match(ctx).result
    return (method, fee(ctx) if callable(fee) else fee)


SAMPLE = [
    (lv, am, w, rg, fr)
    for lv in ("vip", "member", "guest")
    for rg in ("local", "remote", "overseas")
    for fr in (False, True)
    for am in (0, 200, 300, 500, 1000)
    for w in (0, 5, 10, 15, 20, 30, 50)
]


class TestOrdering(unittest.TestCase):
    def test_declaration_order_irrelevant(self):
        baseline = Engine(list(RULES))
        expected = [decide_with(baseline, *c) for c in SAMPLE]
        rng = random.Random(20260926)
        for _ in range(20):
            shuffled = list(RULES)
            rng.shuffle(shuffled)
            engine = Engine(shuffled)
            actual = [decide_with(engine, *c) for c in SAMPLE]
            self.assertEqual(actual, expected)

    def test_priority_swap_changes_behavior(self):
        # 海外非 vip、weight>30 且 amount>=1000：R05 与 R06 均命中
        case = ("member", 1200, 40, "overseas", False)
        original = Engine(list(RULES))
        self.assertEqual(decide_with(original, *case), ("reject", 0.0))

        swapped = [
            Rule(r.rule_id,
                 82 if r.rule_id == "R05" else
                 83 if r.rule_id == "R06" else r.priority,
                 r.conditions, r.result, r.default)
            for r in RULES
        ]
        changed = Engine(swapped)
        self.assertEqual(decide_with(changed, *case), ("express", 50.0))

    def test_priority_swap_remote_changes_behavior(self):
        # 偏远 vip、amount>=500 且 weight>15：R08 与 R09 均命中
        case = ("vip", 600, 20, "remote", False)
        original = Engine(list(RULES))
        self.assertEqual(decide_with(original, *case), ("express", 0.0))

        swapped = [
            Rule(r.rule_id,
                 69 if r.rule_id == "R08" else
                 70 if r.rule_id == "R09" else r.priority,
                 r.conditions, r.result, r.default)
            for r in RULES
        ]
        changed = Engine(swapped)
        self.assertEqual(decide_with(changed, *case), ("freight", 100.0))


if __name__ == "__main__":
    unittest.main()
