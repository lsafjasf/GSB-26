import datetime
import unittest

from tplcheck import RenderError, render


class TestRenderer(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render("", {}), "")

    def test_text_only(self):
        self.assertEqual(render("hello", {}), "hello")

    def test_basic_placeholder(self):
        self.assertEqual(render("Hi {name}", {"name": "Bo"}), "Hi Bo")

    def test_missing_arg_raises(self):
        with self.assertRaises(RenderError):
            render("Hi {name}", {})

    def test_none_arg_raises_unless_optional(self):
        with self.assertRaises(RenderError):
            render("{x}", {"x": None})
        self.assertEqual(render("{x?}", {"x": None}), "")
        self.assertEqual(render("{x?}", {}), "")

    def test_type_enforcement(self):
        with self.assertRaises(RenderError):
            render("{n:int}", {"n": "not an int"})
        with self.assertRaises(RenderError):
            render("{n:int}", {"n": True})  # bool is not int
        self.assertEqual(render("{n:int}", {"n": 3}), "3")
        self.assertEqual(render("{n:float}", {"n": 3}), "3")

    def test_bool_formatting(self):
        self.assertEqual(render("{b:bool}", {"b": True}), "true")

    def test_date_formatting(self):
        self.assertEqual(
            render("{d:date}", {"d": datetime.date(2026, 9, 27)}), "2026-09-27"
        )

    def test_if_section(self):
        tpl = "{#if vip}VIP{/if} user"
        self.assertEqual(render(tpl, {"vip": True}), "VIP user")
        self.assertEqual(render(tpl, {"vip": False}), " user")
        with self.assertRaises(RenderError):
            render(tpl, {"vip": "yes"})

    def test_each_section(self):
        tpl = "{#each xs as x}[{x:int}]{/each}"
        self.assertEqual(render(tpl, {"xs": [1, 2, 3]}), "[1][2][3]")
        self.assertEqual(render(tpl, {"xs": []}), "")
        with self.assertRaises(RenderError):
            render(tpl, {"xs": "notalist"})

    def test_loop_var_not_visible_after_section(self):
        with self.assertRaises(RenderError):
            render("{#each xs as x}{x}{/each}{x}", {"xs": [1]})

    def test_nested_loops(self):
        tpl = "{#each gs as g}({#each g as v}{v:int}{/each}){/each}"
        self.assertEqual(render(tpl, {"gs": [[1, 2], [3]]}), "(12)(3)")

    def test_escaped_braces(self):
        self.assertEqual(render("{{x}}", {}), "{x}")


if __name__ == "__main__":
    unittest.main()
