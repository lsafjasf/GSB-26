import unittest

from tplcheck import validate


def kinds(result):
    return [e.kind for e in result.errors]


class TestValidator(unittest.TestCase):
    def test_empty_template_is_valid(self):
        r = validate("")
        self.assertTrue(r.ok)
        self.assertEqual(r.signature, {})

    def test_no_placeholders(self):
        r = validate("just text, nothing else")
        self.assertTrue(r.ok)
        self.assertEqual(r.order, [])

    def test_signature_order_and_types(self):
        r = validate("{b:int} {a:str} {c}")
        self.assertTrue(r.ok)
        self.assertEqual(r.order, ["b", "a", "c"])
        self.assertEqual(r.signature["a"].type, "str")
        self.assertEqual(r.signature["c"].type, "any")

    def test_repeated_placeholder_same_type(self):
        r = validate("{x:int} and again {x:int} and {x}")
        self.assertTrue(r.ok)
        self.assertEqual(r.order, ["x"])
        self.assertEqual(r.signature["x"].type, "int")

    def test_type_conflict(self):
        r = validate("{x:int} {x:str}")
        self.assertFalse(r.ok)
        self.assertIn("TYPE_CONFLICT", kinds(r))
        self.assertEqual(r.errors[0].pos.line, 1)

    def test_if_condition_must_be_bool(self):
        r = validate("{f:str} {#if f}x{/if}")
        self.assertFalse(r.ok)
        self.assertIn("TYPE_CONFLICT", kinds(r))

    def test_each_source_must_be_list(self):
        r = validate("{n:int} {#each n as i}{i}{/each}")
        self.assertFalse(r.ok)
        self.assertIn("TYPE_CONFLICT", kinds(r))

    def test_loop_var_scoped_ok(self):
        r = validate("{#each xs as x}{x:str}{/each}")
        self.assertTrue(r.ok)
        self.assertEqual(r.order, ["xs"])
        self.assertEqual(r.loop_var_types["x"], "str")

    def test_loop_var_out_of_scope(self):
        r = validate("{#each xs as x}{x}{/each} oops {x}")
        self.assertFalse(r.ok)
        self.assertIn("OUT_OF_SCOPE", kinds(r))
        err = r.errors[0]
        self.assertIn("'x'", err.message)

    def test_loop_var_out_of_scope_in_if(self):
        r = validate("{#each xs as x}{x}{/each}{#if x}bad{/if}")
        self.assertFalse(r.ok)
        self.assertIn("OUT_OF_SCOPE", kinds(r))

    def test_nested_loops_inner_var_not_visible_outside(self):
        src = "{#each a as x}{#each b as y}{y}{/each}{/each}{y}"
        r = validate(src)
        self.assertFalse(r.ok)
        self.assertIn("OUT_OF_SCOPE", kinds(r))

    def test_deeply_nested_sections_ok(self):
        src = (
            "{#if a}{#each xs as x}{#if b}{#each ys as y}{#if c}"
            "{x:str}-{y:int}"
            "{/if}{/each}{/if}{/each}{/if}"
        )
        r = validate(src)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.order, ["a", "xs", "b", "ys", "c"])

    def test_shadowing_loop_var(self):
        src = "{#each xs as x}{#each x as x}{x:int}{/each}{/each}"
        r = validate(src)
        self.assertTrue(r.ok, r.errors)

    def test_optional_flag(self):
        r = validate("{nick:str?}")
        self.assertTrue(r.ok)
        self.assertTrue(r.signature["nick"].optional)

    def test_optional_inconsistency_warns(self):
        r = validate("{a?} {a}")
        self.assertTrue(r.ok)
        self.assertEqual([w.kind for w in r.warnings], ["OPTIONAL_INCONSISTENT"])
        self.assertFalse(r.signature["a"].optional)

    def test_positions_are_reported(self):
        r = validate("line1\n{x:int} {x:str}")
        self.assertFalse(r.ok)
        self.assertEqual((r.errors[0].pos.line, r.errors[0].pos.column), (2, 9))


if __name__ == "__main__":
    unittest.main()
