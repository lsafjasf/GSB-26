"""类型检查器自测：覆盖空程序、单表达式、递归、互递归、多态、
类型冲突、递归类型终止、模糊类型报告与大规模程序终止性。"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typechecker import check_source, parse_program
from typechecker.infer import Checker

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def check(src):
    return check_source(src)


class TestBasics(unittest.TestCase):
    def test_empty_program(self):
        result = check("")
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.def_types, {})

    def test_single_expression(self):
        result = check("42")
        self.assertTrue(result.ok)
        self.assertEqual(result.expr_types, ["Int"])

    def test_single_bool_and_string(self):
        result = check('true\n"hello"')
        self.assertEqual(result.expr_types, ["Bool", "Str"])

    def test_unbound_variable(self):
        result = check("(def x y)")
        self.assertFalse(result.ok)
        self.assertIn("未绑定的变量", result.errors[0].message)


class TestRecursion(unittest.TestCase):
    def test_recursive_factorial(self):
        result = check("""
(defrec (fact (fn n (if (= n 0) 1 (* n (fact (- n 1)))))))
(def f5 (fact 5))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["fact"], "(Int -> Int)")
        self.assertEqual(result.def_types["f5"], "Int")

    def test_mutual_recursion(self):
        result = check("""
(defrec
  (even? (fn n (if (= n 0) true  (odd?  (- n 1)))))
  (odd?  (fn n (if (= n 0) false (even? (- n 1))))))
(def r (even? 10))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["even?"], "(Int -> Bool)")
        self.assertEqual(result.def_types["odd?"], "(Int -> Bool)")
        self.assertEqual(result.def_types["r"], "Bool")

    def test_polymorphic_recursion_len(self):
        result = check("""
(defrec (len (fn xs (if (nil? xs) 0 (+ 1 (len (tail xs)))))))
(def a (len (list 1 2 3)))
(def b (len (list true false)))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["a"], "Int")
        self.assertEqual(result.def_types["b"], "Int")

    def test_mutual_recursion_conflict(self):
        result = check("""
(defrec
  (is-zero? (fn n (= n 0)))
  (flip (fn (b : Bool) (is-zero? b))))
""")
        self.assertFalse(result.ok)
        self.assertIn("期望 Int，实际 Bool", result.errors[0].message)


class TestPolymorphism(unittest.TestCase):
    def test_identity_used_at_two_types(self):
        result = check("""
(def id (fn x x))
(def a (id 1))
(def b (id true))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["a"], "Int")
        self.assertEqual(result.def_types["b"], "Bool")

    def test_generic_builtins(self):
        result = check("""
(def nums (cons 1 (cons 2 nil)))
(def flags (cons true nil))
(def h (head nums))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["nums"], "(List Int)")
        self.assertEqual(result.def_types["flags"], "(List Bool)")
        self.assertEqual(result.def_types["h"], "Int")

    def test_higher_order(self):
        result = check("""
(def apply2 (fn f (fn x (f (f x)))))
(def plus2 (apply2 (fn n (+ n 1))))
(def negate2 (apply2 not))
""")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["plus2"], "(Int -> Int)")
        self.assertEqual(result.def_types["negate2"], "(Bool -> Bool)")

    def test_annotation_with_placeholder(self):
        result = check("(def id (: (-> 'a 'a)) (fn x x))\n(def n (id 3))")
        self.assertTrue(result.ok)
        self.assertEqual(result.def_types["n"], "Int")


class TestTypeConflicts(unittest.TestCase):
    def test_call_mismatch_reports_location_and_chain(self):
        result = check("(def f (fn (x : Bool) x))\n(def y (f 1))")
        self.assertFalse(result.ok)
        err = result.errors[0]
        self.assertIn("期望 Bool，实际 Int", err.message)
        self.assertEqual((err.loc.line, err.loc.col), (2, 8))  # (f 1) 的位置
        self.assertTrue(any("类型标注 Bool" in e for e in err.chain))
        self.assertTrue(any("整数字面量 1" in e for e in err.chain))

    def test_if_branch_mismatch(self):
        result = check("(def x (if true 1 false))")
        self.assertFalse(result.ok)
        self.assertIn("期望 Int，实际 Bool", result.errors[0].message)

    def test_annotation_mismatch(self):
        result = check("(def x (: Bool) 3)")
        self.assertFalse(result.ok)
        err = result.errors[0]
        self.assertIn("期望 Bool，实际 Int", err.message)
        self.assertTrue(any("类型标注" in e for e in err.chain))

    def test_list_element_mismatch(self):
        result = check("(def xs (list 1 true))")
        self.assertFalse(result.ok)
        self.assertIn("期望 Int，实际 Bool", result.errors[0].message)

    def test_if_condition_not_bool(self):
        result = check("(def x (if 1 2 3))")
        self.assertFalse(result.ok)
        self.assertIn("期望 Bool，实际 Int", result.errors[0].message)

    def test_multiple_errors_reported(self):
        result = check("(def a (: Bool) 1)\n(def b (: Int) false)")
        self.assertEqual(len(result.errors), 2)


class TestTermination(unittest.TestCase):
    def test_recursive_type_rejected(self):
        # (fn x (x x)) 会产生 'a = ('a -> 'b)，occurs check 必须报错而非展开
        result = check("(def w (fn x (x x)))")
        self.assertFalse(result.ok)
        self.assertIn("递归类型", result.errors[0].message)

    def test_recursive_type_in_list(self):
        result = check("(def w (fn x (cons x x)))")
        self.assertFalse(result.ok)
        self.assertIn("递归类型", result.errors[0].message)

    def test_mutual_recursion_terminates(self):
        # 互递归 + occurs 压力：必须在有限时间内返回
        src = "(defrec\n" + "\n".join(
            f"  (f{i} (fn n (if (= n 0) 0 (f{(i + 1) % 50} (- n 1)))))"
            for i in range(50)) + ")\n(def r (f0 10))"
        start = time.perf_counter()
        result = check(src)
        elapsed = time.perf_counter() - start
        self.assertTrue(result.ok)
        self.assertLess(elapsed, 5.0)

    def test_deep_expression_terminates(self):
        src = "(+ 1 " * 2000 + "1" + ")" * 2000
        result = check(src)
        self.assertTrue(result.ok)
        self.assertEqual(result.expr_types, ["Int"])

    def test_large_program_terminates(self):
        # 5000 个定义、约 4 万节点，线性时间内完成
        lines = ["(def f0 (fn x (+ x 1)))"]
        for i in range(1, 5000):
            lines.append(f"(def f{i} (fn x (f{i - 1} (f{i - 1} x))))")
        src = "\n".join(lines)
        start = time.perf_counter()
        result = check(src)
        elapsed = time.perf_counter() - start
        self.assertTrue(result.ok)
        self.assertLess(elapsed, 30.0)


class TestAmbiguity(unittest.TestCase):
    def test_nil_is_reported_not_defaulted(self):
        result = check("(def empty nil)")
        self.assertTrue(result.ok)  # 不是错误，但必须显式报告
        self.assertTrue(result.warnings)
        self.assertIn("无法推断为具体类型", result.warnings[0].message)
        self.assertIn("未默认为 Any", result.warnings[0].message)

    def test_polymorphic_placeholder_is_explicit(self):
        result = check("(def id (fn x x))")
        self.assertTrue(result.ok)
        self.assertTrue(any("多态泛型占位" in d.message for d in result.infos))

    def test_fully_concrete_program_has_no_ambiguity(self):
        result = check("(def f (fn x (+ x 1)))\n(def y (f 2))")
        self.assertTrue(result.ok)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.infos, [])


class TestExamples(unittest.TestCase):
    def _run(self, name):
        path = os.path.join(EXAMPLES, name)
        with open(path, encoding="utf-8") as fh:
            return check(fh.read())

    def test_ok_examples_have_no_errors(self):
        for name in ("ok_polymorphism.dsl", "ok_recursion.dsl"):
            result = self._run(name)
            self.assertTrue(result.ok, f"{name}: {result.errors}")

    def test_err_examples_have_errors(self):
        for name in ("err_call_mismatch.dsl", "err_branch_mismatch.dsl",
                     "err_recursive_type.dsl", "err_annotation.dsl",
                     "err_mutual_recursion.dsl"):
            result = self._run(name)
            self.assertFalse(result.ok, f"{name} 应当报错")
            for err in result.errors:
                self.assertIsNotNone(err.loc)
                self.assertTrue(err.chain, f"{name} 错误缺少约束链")

    def test_warn_example_has_warnings(self):
        result = self._run("warn_ambiguous.dsl")
        self.assertTrue(result.ok)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
