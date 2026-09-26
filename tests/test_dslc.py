"""Self tests for the dslc compiler. Run: python3 -m unittest discover -s tests -v"""

import os
import random
import unittest

from dslc.compiler import compile_plan
from dslc.errors import DSLError, DSLErrorList
from dslc.interp import (InputError, effectful_sequence, execute,
                         traces_equivalent)
from dslc.lexer import tokenize
from dslc.parser import parse_config

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def compile_text(text, optimize=True):
    return compile_plan(parse_config(tokenize(text)), optimize=optimize)


def errors_of(text):
    try:
        compile_text(text)
    except DSLErrorList as e:
        return [str(x) for x in e.errors]
    except DSLError as e:
        return [str(e)]
    return []


def run_both(text, inputs=None):
    plan_l = compile_text(text, optimize=False)
    plan_o = compile_text(text, optimize=True)
    tl = execute(plan_l, inputs)
    to = execute(plan_o, inputs)
    return (plan_l, tl), (plan_o, to)


class ShapeTests(unittest.TestCase):
    def test_empty_config(self):
        plan = compile_text("")
        self.assertEqual(plan["stages"], [])
        self.assertEqual(plan["steps"], {})
        trace = execute(plan)
        self.assertEqual(effectful_sequence(plan, trace), [])

    def test_comment_only_config(self):
        plan = compile_text("# nothing here\n\n# really\n")
        self.assertEqual(plan["stages"], [])

    def test_single_step(self):
        plan = compile_text('step hello { run: "echo hi" }')
        self.assertEqual(plan["stages"], [["hello"]])
        trace = execute(plan)
        self.assertEqual(effectful_sequence(plan, trace), ["hello"])

    def test_deep_chain(self):
        n = 300
        text = "\n".join(
            "step s{} {{{} run: \"x\" }}".format(
                i, " needs: s{}".format(i - 1) if i else "")
            for i in range(n))
        plan = compile_text(text)
        self.assertEqual(len(plan["stages"]), n)
        self.assertTrue(all(len(st) == 1 for st in plan["stages"]))
        trace = execute(plan)
        self.assertEqual(effectful_sequence(plan, trace),
                         ["s{}".format(i) for i in range(n)])

    def test_duplicate_step_names(self):
        errs = errors_of(
            'step a { run: "1" }\nstep b { run: "2" }\nstep a { run: "3" }\n')
        self.assertEqual(len(errs), 1)
        self.assertIn("duplicate step name 'a'", errs[0])
        self.assertIn("3:1", errs[0])       # error position
        self.assertIn("1:1", errs[0])       # first definition position

    def test_duplicate_param(self):
        errs = errors_of("param a: int\nparam a: str\n")
        self.assertTrue(any("duplicate parameter 'a'" in e for e in errs))


class ValidationTests(unittest.TestCase):
    def test_cycle(self):
        errs = errors_of(
            'step a { needs: c run: "x" }\n'
            'step b { needs: a run: "x" }\n'
            'step c { needs: b run: "x" }\n')
        self.assertTrue(any("dependency cycle" in e for e in errs))
        self.assertTrue(any("a ->" in e or "b ->" in e for e in errs))

    def test_self_cycle(self):
        errs = errors_of('step a { needs: a run: "x" }')
        self.assertTrue(any("dependency cycle" in e and "a -> a" in e
                            for e in errs))

    def test_unknown_needs(self):
        errs = errors_of('step a { needs: ghost run: "x" }')
        self.assertTrue(any("undefined step 'ghost'" in e for e in errs))

    def test_unknown_branch_target(self):
        errs = errors_of(
            "param env: enum(a, b) = a\n"
            "step r { branch on env { case a -> ghost case b -> skip } }\n")
        self.assertTrue(any("undefined step 'ghost'" in e for e in errs))

    def test_unknown_param_in_when(self):
        errs = errors_of("step a { when: missing > 1 run: \"x\" }")
        self.assertTrue(any("undefined parameter or unknown value" in e
                            for e in errs))

    def test_unknown_param_in_args(self):
        errs = errors_of('step a { args: ghost=1 run: "x" }')
        self.assertTrue(any("undefined parameter 'ghost'" in e for e in errs))

    def test_default_type_mismatch(self):
        errs = errors_of('param n: int = "three"\n')
        self.assertTrue(any("expected int, got str" in e for e in errs))

    def test_args_type_mismatch(self):
        errs = errors_of(
            'param n: int = 1\nstep a { args: n="many" run: "x" }')
        self.assertTrue(any("expected int, got str" in e for e in errs))

    def test_when_not_bool(self):
        errs = errors_of('param n: int = 1\nstep a { when: n run: "x" }')
        self.assertTrue(any("must be of type bool" in e for e in errs))

    def test_compare_type_mismatch(self):
        errs = errors_of(
            'param n: int = 1\nstep a { when: n == "x" run: "y" }')
        self.assertTrue(any("cannot compare int with str" in e for e in errs))

    def test_logic_type_mismatch(self):
        errs = errors_of(
            'param n: int = 1\nstep a { when: n and true run: "y" }')
        self.assertTrue(any("requires bool operands" in e for e in errs))

    def test_branch_coverage_enum_missing(self):
        errs = errors_of(
            "param env: enum(a, b, c) = a\n"
            "step r { branch on env { case a -> skip case b -> skip } }\n")
        self.assertTrue(any("not exhaustive" in e and "'c'" in e
                            for e in errs))

    def test_branch_requires_else_for_str(self):
        errs = errors_of(
            'param r: str = "x"\n'
            'step s { branch on r { case "x" -> skip } }\n')
        self.assertTrue(any("requires an 'else' case" in e for e in errs))

    def test_branch_bool_full_coverage_ok(self):
        plan = compile_text(
            "param f: bool = true\n"
            "step r { branch on f { case true -> skip case false -> skip } }\n")
        self.assertEqual(plan["steps"], {})  # empty branch eliminated

    def test_branch_duplicate_case(self):
        errs = errors_of(
            "param env: enum(a, b) = a\n"
            "step r { branch on env { case a -> skip case a -> skip "
            "else -> skip } }\n")
        self.assertTrue(any("duplicate case label" in e for e in errs))

    def test_case_label_type_mismatch(self):
        errs = errors_of(
            "param env: enum(a, b) = a\n"
            'step r { branch on env { case 1 -> skip else -> skip } }\n')
        self.assertTrue(any("does not match branch type" in e for e in errs))

    def test_error_positions(self):
        errs = errors_of('step a { needs: ghost run: "x" }')
        self.assertTrue(errs[0].startswith("1:17"))


class OptimizeTests(unittest.TestCase):
    def test_merge_parallel(self):
        text = "\n".join('step s{} {{ run: "x" }}'.format(i) for i in range(3))
        plan_l = compile_text(text, optimize=False)
        plan_o = compile_text(text, optimize=True)
        self.assertEqual(len(plan_l["stages"]), 3)
        self.assertEqual(plan_o["stages"], [["s0", "s1", "s2"]])

    def test_noop_elimination(self):
        text = (
            'step a { run: "a" }\n'
            "step mid { needs: a }\n"
            'step b { needs: mid run: "b" }\n')
        plan = compile_text(text)
        self.assertNotIn("mid", plan["steps"])
        self.assertEqual(plan["steps"]["b"]["needs"], ["a"])

    def test_empty_branch_elimination(self):
        text = (
            "param env: enum(a, b) = a\n"
            'step before { run: "x" }\n'
            "step r { needs: before branch on env {\n"
            "  case a -> skip\n  case b -> skip } }\n"
            'step after { needs: r run: "y" }\n')
        plan = compile_text(text)
        self.assertNotIn("r", plan["steps"])
        self.assertEqual(plan["steps"]["after"]["needs"], ["before"])

    def test_const_guard_folded(self):
        text = (
            'step a { when: 1 < 2 run: "a" }\n'
            'step b { when: 1 > 2 run: "b" }\n')
        plan = compile_text(text)
        self.assertIn("a", plan["steps"])
        self.assertIsNone(plan["steps"]["a"]["when"])
        self.assertNotIn("b", plan["steps"])  # dead and unreferenced

    def test_optimization_shrinks_stages(self):
        text = "\n".join([
            'step a { run: "a" }',
            'step b { run: "b" }',
            "step n1 { needs: a }",
            "step n2 { needs: n1 }",
            'step c { needs: b, n2 run: "c" }',
        ])
        plan_l = compile_text(text, optimize=False)
        plan_o = compile_text(text, optimize=True)
        self.assertLess(len(plan_o["stages"]), len(plan_l["stages"]))
        self.assertNotIn("n1", plan_o["steps"])
        self.assertNotIn("n2", plan_o["steps"])


class InterpTests(unittest.TestCase):
    def test_branch_selection(self):
        text = (
            "param env: enum(a, b) = a\n"
            "step r { branch on env { case a -> ta case b -> tb } }\n"
            'step ta { run: "A" }\n'
            'step tb { run: "B" }\n')
        plan = compile_text(text)
        self.assertEqual(effectful_sequence(plan, execute(plan, {"env": "a"})),
                         ["ta"])
        self.assertEqual(effectful_sequence(plan, execute(plan, {"env": "b"})),
                         ["tb"])

    def test_guard_skip_cascades(self):
        text = (
            "param flag: bool = false\n"
            'step a { when: flag run: "a" }\n'
            'step b { needs: a run: "b" }\n'
            'step c { run: "c" }\n')
        plan = compile_text(text)
        self.assertEqual(effectful_sequence(plan, execute(plan)), ["c"])
        self.assertEqual(
            effectful_sequence(plan, execute(plan, {"flag": True})),
            ["a", "c", "b"])

    def test_input_type_checked(self):
        plan = compile_text("param n: int = 1\nstep a { run: \"x\" }")
        with self.assertRaises(InputError):
            execute(plan, {"n": "oops"})
        with self.assertRaises(InputError):
            execute(plan, {"unknown": 1})

    def test_join_via_branch_step(self):
        text = (
            "param env: enum(a, b) = a\n"
            "step r { branch on env { case a -> ta case b -> tb } }\n"
            'step ta { run: "A" }\n'
            'step tb { run: "B" }\n'
            'step join_ { needs: r run: "J" }\n')
        plan = compile_text(text)
        self.assertEqual(effectful_sequence(plan, execute(plan, {"env": "b"})),
                         ["join_", "tb"])


class DifferentialTests(unittest.TestCase):
    def test_example_file_all_inputs(self):
        with open(os.path.join(EXAMPLES, "web_deploy.dsl")) as fh:
            text = fh.read()
        plan_l = compile_text(text, optimize=False)
        plan_o = compile_text(text, optimize=True)
        cases = 0
        for env in ("dev", "staging", "prod"):
            for replicas in (0, 1, 2, 5):
                for debug in (False, True):
                    inputs = {"env": env, "replicas": replicas, "debug": debug}
                    tl = execute(plan_l, inputs)
                    to = execute(plan_o, inputs)
                    ok, why = traces_equivalent(plan_l, tl, plan_o, to)
                    self.assertTrue(ok, "{}: {}".format(inputs, why))
                    cases += 1
        self.assertEqual(cases, 24)

    def test_random_configs(self):
        rng = random.Random(20260927)
        for trial in range(300):
            text = random_config(rng)
            inputs = {"env": rng.choice("abc"),
                      "n": rng.randint(0, 3),
                      "flag": rng.choice([True, False])}
            (plan_l, tl), (plan_o, to) = run_both(text, inputs)
            ok, why = traces_equivalent(plan_l, tl, plan_o, to)
            self.assertTrue(ok, "trial {} inputs {}: {}\n{}".format(
                trial, inputs, why, text))
            self.assertLessEqual(len(plan_o["stages"]), len(plan_l["stages"]))


def random_config(rng):
    lines = ["param env: enum(a, b, c) = a",
             "param n: int = 1",
             "param flag: bool = true"]
    count = rng.randint(1, 14)
    for i in range(count):
        name = "s{}".format(i)
        body = []
        if i > 0 and rng.random() < 0.6:
            k = rng.randint(1, min(2, i))
            deps = rng.sample(["s{}".format(j) for j in range(i)], k)
            body.append("needs: " + ", ".join(deps))
        if rng.random() < 0.5:
            body.append('run: "do {}"'.format(name))
        if rng.random() < 0.35:
            body.append("when: " + rng.choice([
                "flag", "not flag", "n > 1", "n <= 2", "env == a",
                "env != b", "flag and n > 0", "flag or n > 5",
                "true", "false", "1 < 2"]))
        if rng.random() < 0.25 and i < count - 1:
            later = ["s{}".format(j) for j in range(i + 1, count)]
            t1 = rng.choice(later)
            t2 = rng.choice(later)
            if rng.random() < 0.5:
                body.append("branch on env {{\n"
                            "  case a -> {}\n"
                            "  case b -> skip\n"
                            "  else -> {}\n"
                            "}}".format(t1, t2))
            else:
                body.append("branch on flag {{\n"
                            "  case true -> {}\n"
                            "  case false -> {}\n"
                            "}}".format(t1, t2))
        lines.append("step {} {{\n  ".format(name) + "\n  ".join(body) + "\n}")
    return "\n".join(lines)


if __name__ == "__main__":
    unittest.main()
