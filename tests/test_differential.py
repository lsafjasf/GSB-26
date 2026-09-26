"""差分回归测试：穷举输入空间（含全部边界值），逐例比对重构前后结果。

同时统计每条规则（含默认规则）的命中次数，断言所有规则均被覆盖，
即原实现的每一个分支（含默认分支）都有用例触达。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import legacy
import refactored
from rules import RULES

LEVELS = ["vip", "member", "guest"]
REGIONS = ["local", "remote", "overseas"]
FRAGILE = [False, True]
# 边界值：覆盖 200 / 300 / 500 / 1000（金额）与 5 / 10 / 15 / 20 / 30（重量）
AMOUNTS = [0, 199.99, 200, 299.99, 300, 499.99, 500, 999.99, 1000, 1500]
WEIGHTS = [0, 5, 5.0001, 10, 10.0001, 15, 15.0001, 20, 20.0001, 30, 30.0001, 50]


def case_space():
    for level in LEVELS:
        for region in REGIONS:
            for fragile in FRAGILE:
                for amount in AMOUNTS:
                    for weight in WEIGHTS:
                        yield (level, amount, weight, region, fragile)


class TestDifferential(unittest.TestCase):
    def test_results_identical_and_all_rules_covered(self):
        hit = {r.rule_id: 0 for r in RULES}
        mismatches = []
        count = 0
        for level, amount, weight, region, fragile in case_space():
            count += 1
            expected = legacy.decide(level, amount, weight, region, fragile)
            actual = refactored.decide(level, amount, weight, region, fragile)
            if expected != actual:
                mismatches.append(
                    (level, amount, weight, region, fragile, expected, actual)
                )
            rule = refactored._engine.match(
                {"level": level, "amount": amount, "weight": weight,
                 "region": region, "fragile": fragile}
            )
            hit[rule.rule_id] += 1

        self.assertEqual(count, 3 * 3 * 2 * 10 * 12)  # 2160 例
        self.assertEqual(mismatches, [],
                         f"{len(mismatches)} 例不一致，首例: {mismatches[:1]}")
        uncovered = [rid for rid, n in hit.items() if n == 0]
        self.assertEqual(uncovered, [],
                         f"以下规则（含默认分支）未被差分用例覆盖: {uncovered}")


if __name__ == "__main__":
    unittest.main()
