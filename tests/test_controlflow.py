"""异常与跳转语义专项测试：异常处理、提前返回、无条件跳转、栈不平衡。

每条用例对重构前后两个实现分别断言精确结果（值/错误类型/步数/输出）。
用法: python3 -m unittest discover -s tests -v   或   python3 tests/test_controlflow.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vm"))
sys.path.insert(0, os.path.dirname(__file__))

import legacy_vm
import table_vm
from programs import CASES, asm

BOTH = [("legacy", legacy_vm.run), ("table", table_vm.run)]

# (用例名, 期望 ok, 期望 value, 期望 error, 期望 steps, 期望 output)
EXPECT = {
    # 异常处理
    "try_throw_caught":   (True, 142, None, 7, ()),
    "try_stack_restore":  (True, 8, None, 10, ()),
    "nested_try":         (True, 5, None, 9, ()),
    "try_no_throw":       (True, 8, None, 5, ()),
    "throw_after_endtry": (False, None, "UncaughtException", 4, ()),
    "uncaught_throw":     (False, None, "UncaughtException", 2, ()),
    "div0_caught":        (True, 0, None, 7, ("ZeroDivisionError",)),
    "div0_uncaught":      (False, None, "ZeroDivisionError", 3, ()),
    "invalid_jump_caught": (True, 4, None, 5, ()),
    # 提前返回
    "early_return_neg":   (True, -1, None, 8, ()),
    "early_return_pos":   (True, 105, None, 8, ()),
    # 无条件跳转
    "jmp_over_dead_code": (True, 3, None, 5, ()),
    "invalid_jump":       (False, None, "InvalidJump", 1, ()),
    "invalid_jump_neg":   (False, None, "InvalidJump", 1, ()),
    # 栈不平衡
    "imbalance_extra":    (False, None, "StackImbalance", 3, ()),
    "imbalance_empty":    (False, None, "StackImbalance", 1, ()),
    "falloff_ok":         (True, 5, None, 1, ()),
    "falloff_imbalance":  (False, None, "StackImbalance", 2, ()),
    # 步数上限
    "infinite_loop":      (False, None, "StepLimitExceeded", 100, ()),
    "step_limit_exact":   (True, 3, None, 4, ()),
    "step_limit_short":   (False, None, "StepLimitExceeded", 2, ()),
}

CASE_MAP = {c.name: c for c in CASES}


class TestControlFlow(unittest.TestCase):
    pass


def _make(case_name):
    def test(self):
        case = CASE_MAP[case_name]
        ok, value, error, steps, output = EXPECT[case_name]
        kwargs = {} if case.step_limit is None else {"step_limit": case.step_limit}
        for impl_name, run in BOTH:
            with self.subTest(case=case_name, impl=impl_name):
                r = run(case.program, case.inputs, **kwargs)
                self.assertEqual(r.ok, ok)
                self.assertEqual(r.value, value)
                self.assertEqual(r.error, error)
                self.assertEqual(r.steps, steps)
                self.assertEqual(r.output, output)
    return test


for _name in EXPECT:
    setattr(TestControlFlow, f"test_{_name}", _make(_name))


class TestJumpSemanticsExtra(unittest.TestCase):
    """补充：跳转/异常组合边角语义。"""

    def test_caught_error_pushes_kind_string(self):
        prog = asm(("try", "h"), ("load", "nope"),
                   (":", "h"), ("halt",))
        for _, run in BOTH:
            r = run(prog)
            self.assertEqual((r.ok, r.value), (True, "UndefinedVariable"))

    def test_handler_popped_after_endtry(self):
        # endtry 后再 throw，不应被已弹出的处理器捕获
        prog = asm(("try", "h"), ("endtry",), ("push", 1), ("throw",),
                   (":", "h"), ("push", 0), ("halt",))
        for _, run in BOTH:
            r = run(prog)
            self.assertEqual((r.ok, r.error), (False, "UncaughtException"))

    def test_jz_not_taken(self):
        prog = asm(("push", 1), ("jz", "a"), ("push", 7), ("halt",),
                   (":", "a"), ("push", 9), ("halt",))
        for _, run in BOTH:
            self.assertEqual(run(prog).value, 7)

    def test_jz_taken(self):
        prog = asm(("push", 0), ("jz", "a"), ("push", 7), ("halt",),
                   (":", "a"), ("push", 9), ("halt",))
        for _, run in BOTH:
            self.assertEqual(run(prog).value, 9)

    def test_both_impls_agree_on_all_cases(self):
        for case in CASES:
            kwargs = {} if case.step_limit is None else {"step_limit": case.step_limit}
            with self.subTest(case=case.name):
                self.assertEqual(legacy_vm.run(case.program, case.inputs, **kwargs),
                                 table_vm.run(case.program, case.inputs, **kwargs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
