#!/usr/bin/env python3
"""归属对拍测试：提取结果与人工标注的金样 JSON 逐字段比对。

用法：
    python3 tests/run_tests.py            # 运行全部测试
    python3 tests/run_tests.py --update   # 重新生成金样（需人工复核后提交）
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docxtract import extract_file  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"
EXPECTED = ROOT / "tests" / "expected"


def _load_golden(sample_path):
    golden = EXPECTED / (sample_path.stem + ".json")
    with open(golden, encoding="utf-8") as fh:
        return json.load(fh)


class GoldenTest(unittest.TestCase):
    """每个样例文件与人工标注的期望结果对拍（条目集合 + 归属关系）。"""


def _make_golden_test(sample):
    def test(self):
        actual = extract_file(str(sample))
        # 文件路径不入金样，避免环境相关差异
        actual["file"] = "<sample>"
        expected = _load_golden(sample)
        self.assertEqual(
            expected, actual,
            "\n样例 {} 与金样不一致".format(sample.name))
    return test


class AttributionInvariants(unittest.TestCase):
    """人工标注的关键归属不变式（防回归的核心断言）。"""

    def test_trailing_comment_not_attached_to_next_def(self):
        # s04：空行隔开的尾部注释必须是游离注释，不得挂到 second 上
        r = extract_file(str(SAMPLES / "s04_between.py"))
        by_name = {e["name"]: e for e in r["entries"]}
        self.assertIsNone(by_name["second"]["leading_comment"])
        self.assertEqual(
            [c["text"] for c in r["unattached_comments"]],
            ["first 的尾部备注：与 second 之间有空行，属于游离注释"])
        # 紧贴 def 的注释必须归属 third
        self.assertEqual(
            by_name["third"]["leading_comment"]["text"],
            "third 的文档注释（紧贴 def，无空行）")

    def test_eol_comment_is_not_doc(self):
        # s03：def 行尾注释进 header_comment，不进入文档正文
        r = extract_file(str(SAMPLES / "s03_trailing.py"))
        f = next(e for e in r["entries"] if e["name"] == "f")
        self.assertEqual(f["header_comment"]["text"], "这是 f 的行尾注释，不是文档")
        self.assertNotIn("行尾注释", f["doc"]["body"])
        inner = {(c["line"], c["position"]) for c in f["inner_comments"]}
        self.assertEqual(inner, {(2, "end_of_line"), (3, "end_of_line")})

    def test_inner_comments_stay_inner(self):
        # s09：函数体内注释归属该函数，不外泄
        r = extract_file(str(SAMPLES / "s09_inner_comments.py"))
        (entry,) = r["entries"]
        self.assertEqual(len(entry["inner_comments"]), 3)
        self.assertEqual(r["unattached_comments"], [])

    def test_decorator_region_comments(self):
        # s06：装饰器上方、装饰器与 def 之间的注释都算 leading
        r = extract_file(str(SAMPLES / "s06_decorators.py"))
        by_name = {e["name"]: e for e in r["entries"]}
        self.assertEqual(by_name["wrapped"]["leading_comment"]["text"],
                         "属于 wrapped 的文档（在装饰器之上）")
        self.assertEqual(by_name["wrapped2"]["leading_comment"]["text"],
                         "装饰器与 def 之间的注释也算 leading")
        self.assertEqual(by_name["wrapped3"]["header_comment"]["text"],
                         "装饰器行尾注释")
        self.assertEqual(by_name["wrapped"]["decorators"], ["deco", "deco"])

    def test_malformed_param_lines_preserved(self):
        # s05：格式不匹配的行保留原文并标记未解析
        r = extract_file(str(SAMPLES / "s05_docstring_styles.py"))
        by_name = {e["name"]: e for e in r["entries"]}
        doc = by_name["malformed"]["doc"]
        unparsed_raw = [u["raw"] for u in doc["unparsed"]]
        self.assertTrue(any("没有遵循" in t for t in unparsed_raw))
        self.assertTrue(any("也不是列表项" in t for t in unparsed_raw))
        self.assertFalse(doc["returns"]["parsed"])
        self.assertIn("没有类型前缀", doc["returns"]["description"])
        # 正常条目不受影响
        self.assertEqual(doc["params"][0]["name"], "a")
        self.assertTrue(doc["params"][0]["parsed"])

    def test_three_docstring_styles(self):
        r = extract_file(str(SAMPLES / "s05_docstring_styles.py"))
        by_name = {e["name"]: e for e in r["entries"]}
        g = by_name["google_style"]["doc"]
        self.assertEqual([p["name"] for p in g["params"]],
                         ["name", "count", "flag"])
        self.assertEqual(g["params"][0]["type"], "str")
        self.assertIn("支持多行描述", g["params"][1]["description"])
        self.assertEqual(g["returns"], {"type": "str",
                                        "description": "拼接结果", "parsed": True})
        self.assertEqual(g["raises"][0]["name"], "ValueError")
        n = by_name["numpy_style"]["doc"]
        self.assertEqual([(p["name"], p["type"]) for p in n["params"]],
                         [("x", "float"), ("y", "float")])
        self.assertEqual(n["returns"]["type"], "float")
        s = by_name["sphinx_style"]["doc"]
        self.assertEqual([(p["name"], p["type"]) for p in s["params"]],
                         [("path", "str"), ("mode", None)])
        self.assertEqual(s["returns"]["type"], "str")

    def test_qualified_names_and_kinds(self):
        # s07：类方法、静态方法、异步方法、嵌套函数的限定名与类别
        r = extract_file(str(SAMPLES / "s07_class_methods.py"))
        qual = {e["qualified_name"]: e["kind"] for e in r["entries"]}
        self.assertEqual(qual["Service"], "class")
        self.assertEqual(qual["Service.start"], "method")
        self.assertEqual(qual["Service.chain"], "method")
        self.assertEqual(qual["Service.fetch"], "async_method")
        self.assertEqual(qual["Service.outer.helper"], "function")
        # 嵌套函数前的注释属于嵌套函数，不属于 outer
        by_qual = {e["qualified_name"]: e for e in r["entries"]}
        self.assertEqual(
            by_qual["Service.outer.helper"]["leading_comment"]["text"],
            "嵌套函数前的注释属于嵌套函数")
        self.assertEqual(by_qual["Service.outer"]["inner_comments"], [])

    def test_non_ascii(self):
        r = extract_file(str(SAMPLES / "s08_non_ascii.py"))
        (entry,) = r["entries"]
        self.assertEqual(entry["name"], "greet")
        self.assertEqual(entry["leading_comment"]["text"],
                         "问候函数：根据名字生成问候语")
        self.assertEqual(entry["doc"]["params"][0]["name"], "名前")
        self.assertIn("你好，世界", entry["doc"]["returns"]["description"])
        self.assertIn("🎉", r["module_docstring"])


def _update_goldens():
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for sample in sorted(SAMPLES.glob("*.py")):
        result = extract_file(str(sample))
        result["file"] = "<sample>"
        out = EXPECTED / (sample.stem + ".json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("updated", out)


def _register_golden_tests():
    for sample in sorted(SAMPLES.glob("*.py")):
        name = "test_golden_" + sample.stem
        setattr(GoldenTest, name, _make_golden_test(sample))


if __name__ == "__main__":
    if "--update" in sys.argv:
        _update_goldens()
        sys.exit(0)
    _register_golden_tests()
    unittest.main(verbosity=2)
