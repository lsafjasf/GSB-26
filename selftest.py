"""框架自测：用三个已知实现验证框架本身。

断言目标：
1. 参考实现 GoodKV 全部通过 —— 框架不产生误报（false positive）。
2. 问题实现 BadKV 的失败用例集合 == 预先埋入的不一致集合 ——
   差异全部被指出（无漏报），且没有多报（无误报）。
3. 近似实现 MinorKV 仅触发 minor 级失败 —— 判定为“有条件替换”。
4. 兼容性判定三档（可替换 / 有条件替换 / 不可替换）各自正确。
5. 未覆盖契约点能被检出。
"""

import unittest

from contractfw import (
    VERDICT_CONDITIONAL,
    VERDICT_INCOMPATIBLE,
    VERDICT_REPLACEABLE,
    judge,
    run_contract,
)
from kv_contract import CASES, REQUIRED_POINTS
from report import failing_ids, uncovered_points
from impls.good_impl import GoodKV
from impls.bad_impl import BadKV
from impls.minor_impl import MinorKV

# BadKV 中故意埋入的全部不一致（见 impls/bad_impl.py 注释）
EXPECTED_BAD_FAILURES = {
    "error.get-missing",                  # 缺失键未抛 KeyNotFoundError
    "error.set-empty-key",                # 空串键未拒绝
    "error.set-nonstring-key",            # 抛了 TypeError 而非 InvalidKeyError
    "boundary.whitespace-key",            # 纯空白键被接受
    "idempotency.delete-twice",           # delete 永远返回 True
    "atomicity.set-many-partial-failure", # 部分写入
    "performance.size-timeout",           # size 超时
}


class TestFrameworkSelfCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good_results = run_contract(GoodKV, "good", CASES)
        cls.bad_results = run_contract(BadKV, "bad", CASES)
        cls.minor_results = run_contract(MinorKV, "minor", CASES)

    def test_reference_impl_no_false_positive(self):
        """参考实现必须全绿：任何失败都是框架或用例的误报。"""
        self.assertEqual([], failing_ids(self.good_results))

    def test_bad_impl_all_deviations_detected_exactly(self):
        """埋入的 7 处不一致必须全部被指出，且不多报一条。"""
        self.assertEqual(EXPECTED_BAD_FAILURES, set(failing_ids(self.bad_results)))

    def test_minor_impl_only_minor_failure(self):
        self.assertEqual({"boundary.whitespace-key"},
                         set(failing_ids(self.minor_results)))

    def test_verdict_levels(self):
        self.assertEqual(VERDICT_REPLACEABLE,
                         judge(self.good_results, CASES).level)
        self.assertEqual(VERDICT_INCOMPATIBLE,
                         judge(self.bad_results, CASES).level)
        self.assertEqual(VERDICT_CONDITIONAL,
                         judge(self.minor_results, CASES).level)

    def test_bad_impl_failure_details_are_specific(self):
        """失败结果必须带可读的差异描述（期望 vs 实际）。"""
        for r in self.bad_results:
            if not r.passed:
                self.assertTrue(r.failures, f"{r.case_id} 缺少差异描述")
                self.assertTrue(any("期望" in f for f in r.failures),
                                f"{r.case_id} 差异描述不含期望/实际对比")

    def test_uncovered_point_detected(self):
        self.assertEqual(["concurrency.thread-safety"],
                         uncovered_points(REQUIRED_POINTS, CASES))

    def test_timeout_is_failure_not_crash(self):
        """超时用例以 fail 收场，框架自身不崩溃。"""
        result = {r.case_id: r for r in self.bad_results}["performance.size-timeout"]
        self.assertEqual("fail", result.status)
        self.assertTrue(any("超时" in f for f in result.failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
