"""归属对拍测试：提取结果与人工标注的 expected/*.json 逐字段比对。

比较方式为“子集对拍”：expected 中出现的字段必须与提取结果完全一致
（dict 递归包含、list 逐项相等），expected 未写的字段不检查。
"""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docextract import extract_file  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"
EXPECTED = ROOT / "tests" / "expected"


def assert_subset(testcase, expected, actual, path=""):
    if isinstance(expected, dict):
        testcase.assertIsInstance(actual, dict, f"{path}: 期望 dict")
        for key, value in expected.items():
            testcase.assertIn(key, actual, f"{path}.{key}: 字段缺失")
            assert_subset(testcase, value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        testcase.assertIsInstance(actual, list, f"{path}: 期望 list")
        testcase.assertEqual(
            len(expected), len(actual),
            f"{path}: 长度不一致 {len(expected)} != {len(actual)}\n"
            f"expected={expected!r}\nactual={actual!r}")
        for i, (e_item, a_item) in enumerate(zip(expected, actual)):
            assert_subset(testcase, e_item, a_item, f"{path}[{i}]")
    else:
        testcase.assertEqual(expected, actual,
                             f"{path}: {expected!r} != {actual!r}")


class AttributionTest(unittest.TestCase):
    """每个样例文件：条目集合（qualified_name 全集）与归属关系必须一致。"""

    def check_sample(self, name):
        actual = extract_file(SAMPLES / f"{name}.py")
        with open(EXPECTED / f"{name}.json", encoding="utf-8") as fh:
            expected = json.load(fh)

        # 条目集合一致（无遗漏、无多余）
        expected_names = [e["qualified_name"] for e in expected["entries"]]
        actual_names = [e["qualified_name"] for e in actual["entries"]]
        self.assertEqual(expected_names, actual_names,
                         f"{name}: 条目集合不一致")

        if "module" in expected:
            assert_subset(self, expected["module"], actual["module"], "module")
        for exp_entry, act_entry in zip(expected["entries"],
                                        actual["entries"]):
            assert_subset(self, exp_entry, act_entry,
                          exp_entry["qualified_name"])

    def test_attribution(self):
        self.check_sample("attribution")

    def test_no_comments(self):
        self.check_sample("no_comments")

    def test_non_ascii(self):
        self.check_sample("non_ascii")

    def test_docstyles(self):
        self.check_sample("docstyles")


if __name__ == "__main__":
    unittest.main(verbosity=2)
