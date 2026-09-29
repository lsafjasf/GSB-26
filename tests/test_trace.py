"""判定来源追踪测试。

1. 全空间一致性：2160 例穷举，逐例断言追踪结论与实际判定一一对应
   （同一条规则、同一个结果）——追踪与判定使用同一份规则表；
2. 追踪内容：首命中规则、因优先级被跳过的规则、默认分支生效原因；
3. 报告导出：渲染内容包含命中规则与结论，可写入文件。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import refactored
from rule_engine import Engine, export_report, render_report
from rules import RULES

ENGINE = refactored._engine

LEVELS = ["vip", "member", "guest"]
REGIONS = ["local", "remote", "overseas"]
FRAGILE = [False, True]
AMOUNTS = [0, 199.99, 200, 299.99, 300, 499.99, 500, 999.99, 1000, 1500]
WEIGHTS = [0, 5, 5.0001, 10, 10.0001, 15, 15.0001, 20, 20.0001, 30, 30.0001, 50]


def case_space():
    for level in LEVELS:
        for region in REGIONS:
            for fragile in FRAGILE:
                for amount in AMOUNTS:
                    for weight in WEIGHTS:
                        yield (level, amount, weight, region, fragile)


class TestTraceConsistency(unittest.TestCase):
    def test_trace_matches_decision_for_entire_space(self):
        count = 0
        for case in case_space():
            count += 1
            level, amount, weight, region, fragile = case
            ctx = {"level": level, "amount": amount, "weight": weight,
                   "region": region, "fragile": fragile}
            trace = ENGINE.explain(ctx)
            # 追踪的生效规则与 match 的返回必须是同一条规则
            self.assertIs(trace.rule, ENGINE.match(ctx), msg=str(case))
            # 追踪给出的结论与 decide 的实际结论一致
            result, trace2 = refactored.explain(*case)
            self.assertEqual(result, refactored.decide(*case), msg=str(case))
            self.assertEqual(trace2.rule.rule_id, trace.rule.rule_id)
            # 状态机自洽：恰好一条 hit，或全部未命中且落入默认分支
            hits = [e for e in trace.evaluations if e.status == "hit"]
            if trace.is_default:
                self.assertTrue(trace.rule.default)
                self.assertEqual(hits, [])
                self.assertTrue(all(e.status == "not_matched"
                                    for e in trace.evaluations))
            else:
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].rule_id, trace.rule.rule_id)
                # hit 之前的规则全部未命中；被跳过的规则必排在 hit 之后
                pos = {e.rule_id: i for i, e in enumerate(trace.evaluations)}
                for e in trace.evaluations:
                    if pos[e.rule_id] < pos[hits[0].rule_id]:
                        self.assertEqual(e.status, "not_matched")
                    if e.status == "skipped_by_priority":
                        self.assertGreater(pos[e.rule_id],
                                           pos[hits[0].rule_id])
        self.assertEqual(count, 2160)


class TestTraceContent(unittest.TestCase):
    def explain(self, *case):
        return refactored.explain(*case)[1]

    def test_priority_skip_recorded(self):
        # 海外非 vip 超重且大额：R05 命中，R06/R07 条件也满足但被跳过
        trace = self.explain("member", 1500, 40, "overseas", False)
        self.assertEqual(trace.rule.rule_id, "R05")
        self.assertIn("R06", trace.skipped_by_priority)
        self.assertIn("R07", trace.skipped_by_priority)

    def test_priority_skip_remote(self):
        # 偏远 vip 大额且超重：R08 命中，R09/R10 被跳过
        trace = self.explain("vip", 500, 20, "remote", False)
        self.assertEqual(trace.rule.rule_id, "R08")
        self.assertIn("R09", trace.skipped_by_priority)
        self.assertIn("R10", trace.skipped_by_priority)

    def test_default_branch_explained(self):
        trace = self.explain("guest", 100, 3, "local", False)
        self.assertTrue(trace.is_default)
        self.assertEqual(trace.rule.rule_id, "DEFAULT")
        self.assertEqual(trace.skipped_by_priority, ())
        text = trace.render()
        self.assertIn("默认分支", text)
        self.assertIn("16 条业务规则均未命中", text)

    def test_failed_condition_recorded(self):
        trace = self.explain("guest", 100, 3, "local", False)
        r16 = next(e for e in trace.evaluations if e.rule_id == "R16")
        failure = r16.first_failure()
        self.assertEqual(failure.field, "weight")
        self.assertFalse(failure.passed)
        self.assertEqual(failure.actual, 3)


class TestReport(unittest.TestCase):
    CASES = [
        ("member", 1500, 40, "overseas", False),
        ("guest", 100, 3, "local", False),
    ]

    def test_render_and_export(self):
        traces = [refactored.explain(*c)[1] for c in self.CASES]
        text = render_report(traces)
        self.assertIn("判定来源追踪报告", text)
        self.assertIn("R05", text)          # 命中规则
        self.assertIn("被优先级跳过", text)  # R06/R07
        self.assertIn("DEFAULT", text)      # 默认分支
        with tempfile.TemporaryDirectory() as d:
            path = export_report(traces, os.path.join(d, "report.md"))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), text)


if __name__ == "__main__":
    unittest.main()
