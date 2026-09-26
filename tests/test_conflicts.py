"""规则表加载期校验用例：冲突、不可达、默认分支约束。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rule_engine import Engine, Rule, RuleTableError, validate
from rules import RULES

DEFAULT = Rule("D", 0, (), ("standard", 0.0), default=True)


class TestConflicts(unittest.TestCase):
    def test_production_table_is_clean(self):
        self.assertEqual(validate(list(RULES)), [])
        Engine(list(RULES))  # 不抛异常

    def test_conflict_same_priority_overlap(self):
        rules = [
            Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0)),
            Rule("B", 10, (("amount", ">=", 100),), ("reject", 0.0)),
            DEFAULT,
        ]
        with self.assertRaises(RuleTableError) as cm:
            Engine(rules)
        msg = str(cm.exception)
        self.assertIn("CONFLICT", msg)
        self.assertIn("A", msg)
        self.assertIn("B", msg)

    def test_no_conflict_when_priority_differs(self):
        rules = [
            Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0)),
            Rule("B", 9, (("amount", ">=", 100),), ("reject", 0.0)),
            DEFAULT,
        ]
        Engine(rules)  # 重叠但优先级不同 -> 合法

    def test_no_conflict_when_disjoint(self):
        rules = [
            Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0)),
            Rule("B", 10, (("level", "==", "guest"),), ("reject", 0.0)),
            DEFAULT,
        ]
        Engine(rules)  # 同优先级但条件互斥 -> 合法

    def test_unreachable_shadowed(self):
        rules = [
            Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0)),
            Rule("B", 5, (("level", "==", "vip"),
                          ("amount", ">=", 100)), ("drone", 0.0)),
            DEFAULT,
        ]
        with self.assertRaises(RuleTableError) as cm:
            Engine(rules)
        msg = str(cm.exception)
        self.assertIn("UNREACHABLE B", msg)
        self.assertIn("A", msg)

    def test_unreachable_contradictory(self):
        rules = [
            Rule("C", 10, (("weight", ">", 10), ("weight", "<=", 5)),
                 ("express", 0.0)),
            DEFAULT,
        ]
        with self.assertRaises(RuleTableError) as cm:
            Engine(rules)
        self.assertIn("UNREACHABLE C", str(cm.exception))

    def test_missing_default(self):
        rules = [Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0))]
        with self.assertRaises(RuleTableError) as cm:
            Engine(rules)
        self.assertIn("DEFAULT", str(cm.exception))

    def test_duplicate_default(self):
        rules = [
            Rule("A", 10, (("level", "==", "vip"),), ("express", 0.0)),
            Rule("D1", 0, (), ("standard", 0.0), default=True),
            Rule("D2", 0, (), ("standard", 1.0), default=True),
        ]
        with self.assertRaises(RuleTableError) as cm:
            Engine(rules)
        self.assertIn("D1", str(cm.exception))
        self.assertIn("D2", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
