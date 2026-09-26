"""对拍回归测试：同一批程序与输入，同时跑重构前后的解释器。

比较维度：执行状态、返回值、输出序列、错误类型、错误消息、执行步数。
对关键用例 additionally 校验期望结果（绝对正确性，不只是两实现相等）。

运行：
    python3 diff_test.py            # 全部用例
    python3 -m unittest diff_test -v
"""

import unittest

import vm_legacy
import vm_refactored
from programs import (INSTRUCTION_CASES, INTEGRATION_CASES,
                      make_fuzz_cases)

RESULT_FIELDS = ("status", "value", "output", "steps",
                 "error_kind", "error_msg")


def run_both(case):
    legacy = vm_legacy.run(case.program, case.inputs,
                           max_steps=case.max_steps)
    refactored = vm_refactored.run(case.program, case.inputs,
                                   max_steps=case.max_steps)
    return legacy, refactored


def check_case(testcase, case):
    legacy, refactored = run_both(case)
    for field in RESULT_FIELDS:
        testcase.assertEqual(
            getattr(legacy, field), getattr(refactored, field),
            msg="case %r field %r: legacy=%r refactored=%r"
                % (case.name, field, getattr(legacy, field),
                   getattr(refactored, field)))
    for field, expected in case.expect.items():
        testcase.assertEqual(
            getattr(legacy, field), expected,
            msg="case %r: expected %s=%r, got %r"
                % (case.name, field, expected, getattr(legacy, field)))


class InstructionDiffTest(unittest.TestCase):
    """逐指令对拍：每条指令的语义（含错误与步数）前后一致。"""


class IntegrationDiffTest(unittest.TestCase):
    """异常处理 / 提前返回 / 无条件跳转 / 栈不平衡综合对拍。"""


class FuzzDiffTest(unittest.TestCase):
    """固定种子随机程序对拍。"""


class CoverageTest(unittest.TestCase):
    """每条已注册指令都至少被一个逐指令用例覆盖。"""

    def test_all_opcodes_covered(self):
        covered = set()
        for case in INSTRUCTION_CASES:
            covered.update(case.opcodes)
        missing = set(vm_refactored.DISPATCH) - covered
        self.assertEqual(missing, set(),
                         msg="opcodes without instruction case: %r" % missing)


def _make_test(case):
    def test(self):
        check_case(self, case)
    test.__name__ = "test_" + case.name
    test.__doc__ = "program=%r inputs=%r" % (case.program, case.inputs)
    return test


for _case in INSTRUCTION_CASES:
    setattr(InstructionDiffTest, "test_" + _case.name, _make_test(_case))

for _case in INTEGRATION_CASES:
    setattr(IntegrationDiffTest, "test_" + _case.name, _make_test(_case))

for _case in make_fuzz_cases():
    setattr(FuzzDiffTest, "test_" + _case.name, _make_test(_case))


if __name__ == "__main__":
    unittest.main(verbosity=1)
