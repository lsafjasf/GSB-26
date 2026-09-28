"""错误码稳定性契约测试：契约表与注册表一致、对照表新鲜且无跨模块漂移。"""

import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fault_matrix
from refactored.errors import _REGISTRY, Category


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "contracts", "error_codes.json"),
                  encoding="utf-8") as fh:
            cls.contract = json.load(fh)
        with open(os.path.join(ROOT, "contracts", "fault_matrix.json"),
                  encoding="utf-8") as fh:
            cls.matrix = json.load(fh)

    def test_registry_matches_contract(self):
        """删码、改名、私增、分类/可重试/上下文字段漂移都会失败。"""
        self.assertEqual(set(_REGISTRY), set(self.contract["codes"]))
        for code, spec in self.contract["codes"].items():
            actual = _REGISTRY[code]
            self.assertEqual(actual.category.value, spec["category"], code)
            self.assertEqual(actual.retryable, spec["retryable"], code)
            placeholders = set(__import__("re")
                               .findall(r"\{(\w+)\}",
                                        actual.message_template))
            self.assertEqual(placeholders, set(spec["required_context"]),
                             code)

    def test_registry_categories_are_valid_enum(self):
        for code, spec in self.contract["codes"].items():
            self.assertIn(spec["category"],
                          [c.value for c in Category], code)

    def test_matrix_is_fresh(self):
        """对照表必须是当前代码 + 当前契约的产物，禁止手改。"""
        expected = fault_matrix.build_artifacts(
            self.contract, fault_matrix.build_rows(self.contract))
        self.assertEqual(self.matrix, expected)

    def test_every_row_matches(self):
        rows = self.matrix["rows"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["verdict"], "MATCH", row)
            for side in ("legacy", "refactored"):
                view = row[side]
                self.assertEqual(view["code"], row["refactored"]["code"])
                self.assertEqual(view["category"],
                                 row["refactored"]["category"])
                self.assertEqual(view["retryable"],
                                 row["refactored"]["retryable"])

    def test_service_inner_matches_module(self):
        """包装层不得改写下游错误码：服务内层 == 模块级。"""
        by_scenario = {}
        for row in self.matrix["rows"]:
            by_scenario.setdefault(row["scenario"], {})[row["layer"]] = row
        for scenario, layers in by_scenario.items():
            if "service_inner" in layers:
                self.assertEqual(
                    layers["service_inner"]["refactored"]["code"],
                    layers["module"]["refactored"]["code"], scenario)

    def test_same_code_same_semantics_everywhere(self):
        """同一错误码在任意模块/层位/版本侧的分类与可重试性必须一致。"""
        seen = {}
        for row in self.matrix["rows"]:
            for side in ("legacy", "refactored"):
                view = row[side]
                sig = (view["category"], view["retryable"])
                seen.setdefault(view["code"], sig)
                self.assertEqual(seen[view["code"]], sig,
                                 (row["scenario"], row["layer"], side))

    def test_unknown_code_still_rejected_at_runtime(self):
        from refactored.errors import AppError
        with self.assertRaises(ValueError):
            AppError("NOT_IN_CONTRACT", x=1)


if __name__ == "__main__":
    unittest.main()
