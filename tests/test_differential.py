"""Differential tests: validator vs. reference renderer.

Property A: every template the validator *accepts* renders successfully
            with arguments matching its extracted signature.
Property B: every template the validator *rejects* fails at render time
            (RenderError or TemplateSyntaxError) with the same arguments.
"""

import random
import unittest

from tplcheck import RenderError, TemplateSyntaxError, render, validate
from tests.tplgen import TemplateGenerator, gen_args

# fault-injection targets: original type -> conflicting, render-incompatible type
CONFLICT_TYPE = {
    "str": "int",
    "int": "str",
    "float": "str",
    "bool": "int",
    "date": "int",
    "datetime": "int",
    "list": "str",
}

SEED = 20260927
NUM_TEMPLATES = 600


class TestAcceptedTemplatesRender(unittest.TestCase):
    def test_valid_templates_render_successfully(self):
        rng = random.Random(SEED)
        gen = TemplateGenerator(rng)
        checked = 0
        for _ in range(NUM_TEMPLATES):
            src = gen.generate()
            result = validate(src)
            self.assertTrue(
                result.ok,
                f"generator produced invalid template: {src!r} "
                f"{[str(e) for e in result.errors]}",
            )
            args = gen_args(result, rng)
            try:
                render(src, args)
            except RenderError as exc:  # pragma: no cover - failure path
                self.fail(
                    f"validator accepted but renderer failed: {exc}\n"
                    f"template: {src!r}\nargs: {args!r}"
                )
            checked += 1
        self.assertEqual(checked, NUM_TEMPLATES)


class TestRejectedTemplatesFailAtRender(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(SEED + 1)
        self.gen = TemplateGenerator(self.rng)

    def _valid_template(self):
        while True:
            src = self.gen.generate()
            result = validate(src)
            if result.ok and result.signature:
                return src, result

    def test_type_conflict_faults(self):
        for _ in range(200):
            src, result = self._valid_template()
            args = gen_args(result, self.rng)
            candidates = [
                (n, p) for n, p in result.signature.items()
                if p.type in CONFLICT_TYPE
            ]
            if not candidates:
                continue
            name, info = self.rng.choice(candidates)
            bad = f"{src} {{{name}:{CONFLICT_TYPE[info.type]}}}"
            self.assertFalse(validate(bad).ok, bad)
            with self.assertRaises(RenderError, msg=bad):
                render(bad, args)

    def test_out_of_scope_faults(self):
        for _ in range(200):
            src, result = self._valid_template()
            if not result.loop_var_types:
                continue
            args = gen_args(result, self.rng)
            var = self.rng.choice(sorted(result.loop_var_types))
            bad = f"{src} {{{var}}}"
            self.assertFalse(validate(bad).ok, bad)
            with self.assertRaises(RenderError, msg=bad):
                render(bad, args)

    def test_each_over_non_list_faults(self):
        for _ in range(200):
            src, result = self._valid_template()
            args = gen_args(result, self.rng)
            candidates = [
                n for n, p in result.signature.items()
                if p.type in CONFLICT_TYPE and p.type != "list"
            ]
            if not candidates:
                continue
            name = self.rng.choice(candidates)
            bad = f"{src} {{#each {name} as zz}}{{zz}}{{/each}}"
            self.assertFalse(validate(bad).ok, bad)
            with self.assertRaises(RenderError, msg=bad):
                render(bad, args)

    def test_unclosed_section_faults(self):
        for _ in range(100):
            src, result = self._valid_template()
            args = gen_args(result, self.rng)
            bad = f"{src} {{#if someflag}}"
            with self.assertRaises(TemplateSyntaxError):
                validate(bad)
            with self.assertRaises(TemplateSyntaxError):
                render(bad, args)


class TestConcreteDifferentialCases(unittest.TestCase):
    """Fixed side-by-side cases: same args, validator and renderer agree."""

    def test_concrete_cases(self):
        cases = [
            # (template, args, validator_verdict)
            ("", {}, True),
            ("no placeholders", {}, True),
            ("{a} {b} {a}", {"a": 1, "b": 2}, True),
            ("{x:int} {x:str}", {"x": 1}, False),
            ("{#each xs as x}{x}{/each}{x}", {"xs": [1]}, False),
            ("{n:int} {#each n as i}{i}{/each}", {"n": 3}, False),
            ("{f:str} {#if f}x{/if}", {"f": "y"}, False),
            ("{a?} {b}", {"b": "z"}, True),
            (
                "{#if a}{#each xs as x}{#if b}{x:int}{/if}{/each}{/if}",
                {"a": True, "xs": [1, 2], "b": True},
                True,
            ),
        ]
        for src, args, expect_ok in cases:
            with self.subTest(src=src):
                result = validate(src)
                self.assertEqual(result.ok, expect_ok, src)
                if expect_ok:
                    render(src, args)  # must not raise
                else:
                    with self.assertRaises((RenderError, TemplateSyntaxError)):
                        render(src, args)


if __name__ == "__main__":
    unittest.main()
