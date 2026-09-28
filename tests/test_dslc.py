import random
import unittest

from dslc import (
    CompileError,
    PlanInputError,
    compile,
    execute,
    optimize_plan,
    trace_signature,
)
from dslc.testing import chain_config, random_config, random_inputs, wide_config


class BasicCases(unittest.TestCase):
    def test_empty_config(self):
        plan = compile("")
        self.assertEqual(plan["steps"], {})
        self.assertEqual(plan["stages"], [])
        self.assertEqual(execute(plan), [])

    def test_empty_config_with_comments_only(self):
        plan = compile("# just a comment\n\n   \n")
        self.assertEqual(execute(plan), [])

    def test_single_step(self):
        plan = compile('step only {\n  args: { name: "x" }\n}\n')
        self.assertEqual(plan["stages"], [["only"]])
        self.assertEqual(execute(plan), ["only"])

    def test_deep_chain_executes_in_order(self):
        n = 500
        plan = compile(chain_config(n))
        trace = execute(plan)
        self.assertEqual(trace, [f"s{j}" for j in range(n)])

    def test_deep_chain_optimized_keeps_single_stages(self):
        plan = compile(chain_config(20), optimize=True)
        self.assertEqual(plan["stages"], [[f"s{j}"] for j in range(20)])

    def test_wide_config_runs_everything(self):
        plan = compile(wide_config(100), optimize=True)
        self.assertEqual(len(plan["stages"]), 1)
        self.assertEqual(len(plan["stages"][0]), 100)
        self.assertEqual(len(execute(plan)), 100)


class StaticValidation(unittest.TestCase):
    def assert_error(self, text, fragment, line=None):
        with self.assertRaises(CompileError) as ctx:
            compile(text)
        message = str(ctx.exception)
        self.assertIn(fragment, message, message)
        if line is not None:
            self.assertEqual(ctx.exception.line, line)

    def test_duplicate_step_name(self):
        text = "step a {\n}\nstep a {\n}\n"
        self.assert_error(text, "duplicate step name 'a'", line=3)

    def test_duplicate_parameter_name(self):
        text = "param p: int = 1\nparam p: str = \"x\"\n"
        self.assert_error(text, "duplicate parameter name 'p'", line=2)

    def test_step_name_conflicts_with_parameter(self):
        self.assert_error(
            "param p: int = 1\nstep p {\n}\n",
            "conflicts with a parameter",
            line=2,
        )

    def test_direct_cycle(self):
        text = "step a {\n  needs: b;\n}\nstep b {\n  needs: a;\n}\n"
        self.assert_error(text, "cyclic dependency detected: a -> b -> a", line=1)

    def test_self_cycle(self):
        self.assert_error(
            "step a {\n  needs: a;\n}\n", "depends on itself", line=2
        )

    def test_long_cycle_reports_chain(self):
        text = "step s0 {\n  needs: s2;\n}\n"
        text += "step s1 {\n  needs: s0;\n}\n"
        text += "step s2 {\n  needs: s1;\n}\n"
        self.assert_error(text, "s0 -> s2 -> s1 -> s0")

    def test_unknown_dependency(self):
        text = "step a {\n  needs: ghost;\n}\n"
        self.assert_error(text, "unknown step 'ghost'", line=2)

    def test_duplicate_dependency(self):
        text = "step a {\n}\nstep b {\n  needs: a, a;\n}\n"
        self.assert_error(text, "duplicate dependency 'a'", line=4)

    def test_unknown_parameter_reference(self):
        text = "step a {\n  args: { x: $missing }\n}\n"
        self.assert_error(text, "unknown parameter $missing", line=2)

    def test_unknown_branch_parameter(self):
        text = "step a {\n  branch on $nope {\n    true:;\n    false:;\n  }\n}\n"
        self.assert_error(text, "unknown parameter $nope", line=2)

    def test_default_type_mismatch_int(self):
        self.assert_error(
            'param p: int = "nope"\n', "not compatible", line=1
        )

    def test_default_type_mismatch_bool(self):
        self.assert_error(
            "param p: bool = 1\n", "not compatible", line=1
        )

    def test_default_type_mismatch_enum(self):
        self.assert_error(
            'param p: enum(a, b) = c\n', "not compatible", line=1
        )

    def test_float_accepts_int_default(self):
        plan = compile("param p: float = 1\nstep s {\n}\n")
        self.assertEqual(plan["params"]["p"]["default"], 1)

    def test_branch_on_non_discrete_type(self):
        text = (
            'param p: str = "x"\n'
            "step a {\n  branch on $p {\n    x:;\n  }\n}\n"
        )
        self.assert_error(text, "must be bool or enum", line=3)

    def test_branch_invalid_bool_label(self):
        text = (
            "param p: bool = true\n"
            "step a {\n  branch on $p {\n    yes:;\n    false:;\n  }\n}\n"
        )
        self.assert_error(text, "invalid case label 'yes'", line=4)

    def test_branch_invalid_enum_label(self):
        text = (
            "param p: enum(a, b) = a\n"
            "step a {\n  branch on $p {\n    a:;\n    c:;\n  }\n}\n"
        )
        self.assert_error(text, "invalid case label 'c'", line=5)

    def test_branch_bool_coverage_incomplete(self):
        text = (
            "param p: bool = true\n"
            "step a {\n  branch on $p {\n    true:;\n  }\n}\n"
        )
        self.assert_error(text, "not exhaustive: missing false", line=3)

    def test_branch_enum_coverage_incomplete(self):
        text = (
            "param p: enum(a, b, c) = a\n"
            "step a {\n  branch on $p {\n    a:;\n    b:;\n  }\n}\n"
        )
        self.assert_error(text, "not exhaustive: missing c", line=3)

    def test_branch_coverage_ok_with_default(self):
        text = (
            "param p: bool = true\n"
            "step a {\n  branch on $p {\n    true:;\n    _:;\n  }\n}\n"
        )
        plan = compile(text)
        self.assertIn("a", plan["steps"])

    def test_branch_member_unknown_step(self):
        text = (
            "param p: bool = true\n"
            "step a {\n  branch on $p {\n    true: ghost;\n    false:;\n  }\n}\n"
        )
        self.assert_error(text, "references unknown step 'ghost'", line=4)

    def test_syntax_error_has_position(self):
        self.assert_error("step {\n}", "expected IDENT", line=1)

    def test_multi_token_line_error_col_matches_source(self):
        # 同一行里有多个记号：列号必须随每个字符推进，而不是停在第一个记号处。
        text = (
            "param retries: int = 3\n"
            "step build {\n"
            "  args: { n: $retries, missing: $ghost }\n"
            "}\n"
        )
        with self.assertRaises(CompileError) as ctx:
            compile(text)
        self.assertIn("unknown parameter $ghost", str(ctx.exception))
        marker = "$ghost"
        line_no = text[: text.index(marker)].count("\n") + 1
        line_start = text.rfind("\n", 0, text.index(marker)) + 1
        col = 1
        for ch in text[line_start : line_start + text[line_start:].index(marker)]:
            col += 8 - (col - 1) % 8 if ch == "\t" else 1
        self.assertEqual((ctx.exception.line, ctx.exception.col), (line_no, col))
        self.assertEqual((line_no, col), (3, 33))

    def test_tab_advances_to_next_tab_stop(self):
        # 制表符按 8 列制表位推进：\t 后 "needs" 首字符落在第 9 列。
        text = "step a {\n\tneeds: ghost;\n}\n"
        with self.assertRaises(CompileError) as ctx:
            compile(text)
        self.assertEqual((ctx.exception.line, ctx.exception.col), (2, 16))


class ExecutionSemantics(unittest.TestCase):
    DIAMOND = (
        "step a {\n}\n"
        "step b {\n  needs: a;\n}\n"
        "step c {\n  needs: a;\n}\n"
        "step d {\n  needs: b, c;\n}\n"
    )

    def test_diamond_traces(self):
        plan = compile(self.DIAMOND)
        self.assertEqual(execute(plan), ["a", "b", "c", "d"])

    def test_missing_required_input(self):
        plan = compile("param p: int\nstep s {\n}\n")
        with self.assertRaises(PlanInputError):
            execute(plan, {})

    def test_runtime_input_type_check(self):
        plan = compile("param p: int = 1\nstep s {\n}\n")
        with self.assertRaises(PlanInputError):
            execute(plan, {"p": "not-an-int"})

    def test_runtime_input_enum_check(self):
        plan = compile("param p: enum(a, b) = a\nstep s {\n}\n")
        with self.assertRaises(PlanInputError):
            execute(plan, {"p": "z"})

    def test_branch_activation_true_and_false(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true: yes;\n    false: no;\n  }\n}\n"
            "step yes {\n}\nstep no {\n}\n"
        )
        plan = compile(text)
        self.assertEqual(execute(plan, {"flag": True}), ["gate", "yes"])
        self.assertEqual(execute(plan, {"flag": False}), ["gate", "no"])

    def test_explicit_empty_case_does_not_fall_back(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true:;\n    _: fallback;\n  }\n}\n"
            "step fallback {\n}\n"
        )
        plan = compile(text)
        self.assertEqual(execute(plan, {"flag": True}), ["gate"])
        self.assertEqual(execute(plan, {"flag": False}), ["gate", "fallback"])

    def test_skipped_dependency_blocks_dependents(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true: x;\n    false:;\n  }\n}\n"
            "step x {\n}\n"
            "step y {\n  needs: x;\n}\n"
        )
        plan = compile(text)
        self.assertEqual(execute(plan, {"flag": False}), ["gate"])


class Optimization(unittest.TestCase):
    DIAMOND = (
        "step a {\n}\n"
        "step b {\n  needs: a;\n}\n"
        "step c {\n  needs: a;\n}\n"
        "step d {\n  needs: b, c;\n}\n"
    )

    def test_parallel_stage_merging(self):
        plan = compile(self.DIAMOND, optimize=True)
        self.assertEqual(plan["stages"], [["a"], ["b", "c"], ["d"]])

    def test_empty_case_removed_without_default(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true: x;\n    false:;\n  }\n}\n"
            "step x {\n}\n"
        )
        plan = compile(text, optimize=True)
        self.assertEqual(set(plan["steps"]["gate"]["branch"]["cases"]), {"true"})

    def test_empty_case_kept_when_live_default_exists(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true:;\n    _: x;\n  }\n}\n"
            "step x {\n}\n"
        )
        plan = compile(text, optimize=True)
        self.assertIn("true", plan["steps"]["gate"]["branch"]["cases"])
        self.assertIn("_", plan["steps"]["gate"]["branch"]["cases"])

    def test_branch_with_only_empty_cases_becomes_step(self):
        text = (
            "param flag: bool = true\n"
            "step gate {\n  branch on $flag {\n"
            "    true:;\n    false:;\n  }\n}\n"
        )
        plan = compile(text, optimize=True)
        self.assertIsNone(plan["steps"]["gate"]["branch"])
        self.assertEqual(execute(plan, {"flag": True}), ["gate"])

    def test_example_config_compiles(self):
        with open("examples/deploy.dsl", encoding="utf-8") as handle:
            plan = compile(handle.read())
        trace = execute(plan, {"channel": "nightly", "dry_run": True, "retries": 2})
        self.assertIn("route", trace)
        self.assertNotIn("deploy_stable", trace)
        self.assertNotIn("deploy_canary", trace)

    def test_differential_random_configs(self):
        rng = random.Random(20260927)
        for iteration in range(40):
            text = random_config(rng, rng.randint(5, 80))
            before = compile(text)
            after = optimize_plan(before)
            for _ in range(6):
                inputs = random_inputs(rng)
                t1 = execute(before, inputs)
                t2 = execute(after, inputs)
                self.assertEqual(
                    trace_signature(before, t1),
                    trace_signature(after, t2),
                    msg=f"iteration {iteration}, inputs {inputs}",
                )

    def test_differential_large_random(self):
        rng = random.Random(42)
        text = random_config(rng, 600)
        before = compile(text)
        after = optimize_plan(before)
        inputs = random_inputs(rng)
        self.assertEqual(
            trace_signature(before, execute(before, inputs)),
            trace_signature(after, execute(after, inputs)),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
