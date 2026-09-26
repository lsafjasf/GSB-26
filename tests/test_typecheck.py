import time
import unittest

from typecheck import (Binding, BoolLit, Call, If, IntLit, Lam, Let, LetRec,
                       Pos, Program, StrLit, TypeCheckError, Var,
                       check_program, format_type)


def v(name, line=0, col=0):
    return Var(Pos(line, col), name)


def i(value, line=0, col=0):
    return IntLit(Pos(line, col), value)


def b(value, line=0, col=0):
    return BoolLit(Pos(line, col), value)


def lam(param, body, ann=None, line=0, col=0):
    return Lam(Pos(line, col), param, body, ann)


def app(fn, *args, line=0, col=0):
    node = fn
    for arg in args:
        node = Call(Pos(line, col), node, arg)
    return node


def add(x, y):
    return app(v("add"), x, y)


def build_chain(n):
    """n top-level defs: f0 = \\x. add x 1 ; fi = \\x. f(i-1) (add x 1)."""
    groups = []
    prev = None
    for k in range(n):
        if k == 0:
            value = lam("x", add(v("x"), i(1)))
        else:
            value = lam("x", Call(Pos(), v(prev), add(v("x"), i(1))))
        groups.append([Binding(f"f{k}", value)])
        prev = f"f{k}"
    return Program(groups=groups, main=Call(Pos(), v(prev), i(0)))


def count_nodes(node):
    if node is None:
        return 0
    if isinstance(node, Program):
        return sum(count_nodes(bd.value) for g in node.groups for bd in g) \
            + count_nodes(node.main)
    total = 1
    for value in vars(node).values():
        if isinstance(value, Var) or isinstance(value, (IntLit, BoolLit,
                        StrLit, Lam, Call, Let, LetRec, If)):
            total += count_nodes(value)
    return total


class TestBasics(unittest.TestCase):
    def test_empty_program(self):
        result = check_program(Program())
        self.assertIsNone(result.main_type)
        self.assertEqual(result.diagnostics, [])

    def test_single_literal(self):
        result = check_program(Program(main=i(42)))
        self.assertEqual(result.format_main(), "int")

    def test_single_expression(self):
        result = check_program(Program(main=add(i(1), i(2))))
        self.assertEqual(result.format_main(), "int")

    def test_string_and_bool(self):
        prog = Program(main=If(Pos(), b(True),
                               app(v("concat"), StrLit(Pos(), "a"),
                                    StrLit(Pos(), "b")),
                               StrLit(Pos(), "c")))
        self.assertEqual(check_program(prog).format_main(), "str")

    def test_unbound_variable(self):
        with self.assertRaises(TypeCheckError) as cm:
            check_program(Program(main=v("nope", 3, 5)))
        diag = cm.exception.diagnostic
        self.assertEqual(diag.kind, "unbound")
        self.assertEqual((diag.pos.line, diag.pos.col), (3, 5))


class TestFunctions(unittest.TestCase):
    def test_identity_is_polymorphic(self):
        prog = Program(main=Let(Pos(), "id", lam("x", v("x")),
                                Let(Pos(), "a", app(v("id"), i(1)),
                                    app(v("id"), b(True)))))
        result = check_program(prog)
        self.assertEqual(result.format_main(), "bool")
        self.assertEqual(result.diagnostics, [])

    def test_polymorphic_builtin_eq(self):
        prog = Program(main=If(Pos(), app(v("eq"), i(1), i(2)),
                               app(v("eq"), b(True), b(False)), b(False)))
        self.assertEqual(check_program(prog).format_main(), "bool")

    def test_polymorphic_def_in_env(self):
        prog = Program(groups=[[Binding("id", lam("x", v("x")))]],
                       main=app(v("id"), i(7)))
        result = check_program(prog)
        self.assertEqual(result.format_main(), "int")
        self.assertEqual(format_type(result.env["id"].type), "'a -> 'a")

    def test_annotation_ok(self):
        prog = Program(main=Let(Pos(), "f", lam("x", add(v("x"), i(1))),
                                app(v("f"), i(2)), ann="int -> int"))
        self.assertEqual(check_program(prog).format_main(), "int")

    def test_annotation_generic_placeholder(self):
        prog = Program(groups=[[Binding("const", lam("x", lam("y", v("x"))),
                                        ann="a -> b -> a")]],
                       main=app(v("const"), i(1), b(True)))
        result = check_program(prog)
        self.assertEqual(result.format_main(), "int")
        self.assertEqual(format_type(result.env["const"].type), "'a -> 'b -> 'a")


class TestRecursion(unittest.TestCase):
    def test_recursive_factorial(self):
        cond = app(v("eq"), v("n"), i(0))
        rec = Call(Pos(), v("fact"), app(v("sub"), v("n"), i(1)))
        body = If(Pos(), cond, i(1), app(v("mul"), v("n"), rec))
        prog = Program(main=LetRec(Pos(), [Binding("fact", lam("n", body))],
                                   Call(Pos(), v("fact"), i(10))))
        result = check_program(prog)
        self.assertEqual(result.format_main(), "int")

    def test_mutually_recursive_even_odd(self):
        even = lam("n", If(Pos(), app(v("eq"), v("n"), i(0)), b(True),
                           Call(Pos(), v("odd"), app(v("sub"), v("n"), i(1)))))
        odd = lam("n", If(Pos(), app(v("eq"), v("n"), i(0)), b(False),
                          Call(Pos(), v("even"), app(v("sub"), v("n"), i(1)))))
        prog = Program(groups=[[Binding("even", even), Binding("odd", odd)]],
                       main=Call(Pos(), v("even"), i(10)))
        result = check_program(prog)
        self.assertEqual(result.format_main(), "bool")
        self.assertEqual(format_type(result.env["even"].type), "int -> bool")
        self.assertEqual(format_type(result.env["odd"].type), "int -> bool")

    def test_occurs_check_terminates(self):
        # \x. x x  would need an infinite type; must fail, not hang.
        prog = Program(main=lam("x", Call(Pos(), v("x"), v("x"))))
        with self.assertRaises(TypeCheckError) as cm:
            check_program(prog)
        self.assertEqual(cm.exception.diagnostic.kind, "occurs")

    def test_recursive_infinite_type_terminates(self):
        # letrec f = \x. f f : the recursive call references the pre-bound
        # variable, the occurs check rejects it in O(1) constraints.
        prog = Program(main=LetRec(Pos(),
                                   [Binding("f", lam("x", Call(Pos(), v("f"),
                                                              v("f"))))],
                                   i(0)))
        with self.assertRaises(TypeCheckError) as cm:
            check_program(prog)
        self.assertEqual(cm.exception.diagnostic.kind, "occurs")


class TestConflicts(unittest.TestCase):
    def test_simple_conflict(self):
        inner = app(v("add", 1, 1), i(1, 1, 5))
        main = Call(Pos(1, 7), inner, b(True, 1, 11))
        with self.assertRaises(TypeCheckError) as cm:
            check_program(Program(main=main))
        diag = cm.exception.diagnostic
        self.assertEqual(diag.kind, "conflict")
        self.assertEqual(diag.expected, "int")
        self.assertEqual(diag.actual, "bool")
        self.assertEqual((diag.pos.line, diag.pos.col), (1, 7))
        self.assertTrue(diag.chain)

    def test_if_branch_mismatch(self):
        main = If(Pos(2, 3), b(True), i(1), b(False))
        with self.assertRaises(TypeCheckError) as cm:
            check_program(Program(main=main))
        diag = cm.exception.diagnostic
        self.assertEqual(diag.expected, "int")
        self.assertEqual(diag.actual, "bool")
        self.assertEqual((diag.pos.line, diag.pos.col), (2, 3))

    def test_annotation_mismatch(self):
        main = Let(Pos(4, 1), "x", i(1), v("x"), ann="bool")
        with self.assertRaises(TypeCheckError) as cm:
            check_program(Program(main=main))
        diag = cm.exception.diagnostic
        self.assertEqual(diag.expected, "bool")
        self.assertEqual(diag.actual, "int")

    def test_constraint_chain_through_polymorphism(self):
        # let apply = \f. \x. f x in apply (\x. add x 1) true
        apply_fn = lam("f", lam("x", app(v("f"), v("x"))))
        main = Let(Pos(), "apply", apply_fn,
                   app(v("apply"), lam("x", add(v("x"), i(1))), b(True)))
        with self.assertRaises(TypeCheckError) as cm:
            check_program(Program(main=main))
        diag = cm.exception.diagnostic
        self.assertEqual(diag.expected, "int")
        self.assertEqual(diag.actual, "bool")
        reasons = [c.reason for c in diag.chain]
        self.assertGreaterEqual(len(diag.chain), 2)
        self.assertTrue(all(r == "function application" for r in reasons))
        rendered = diag.render()
        self.assertIn("constraint chain", rendered)
        self.assertIn("expected: int", rendered)


class TestUninferred(unittest.TestCase):
    def test_unused_parameter_reported(self):
        result = check_program(Program(main=lam("x", i(1), line=5, col=2)))
        kinds = [d.kind for d in result.diagnostics]
        self.assertIn("uninferred", kinds)
        diag = result.diagnostics[0]
        self.assertIn("'x'", diag.message)
        self.assertEqual((diag.pos.line, diag.pos.col), (5, 2))

    def test_used_unannotated_parameter_not_reported(self):
        result = check_program(Program(main=lam("x", v("x"))))
        self.assertEqual(result.diagnostics, [])
        self.assertEqual(result.format_main(), "'a -> 'a")

    def test_uninferred_top_level_def(self):
        prog = Program(groups=[[Binding("const1", lam("x", i(1)))]])
        result = check_program(prog)
        self.assertEqual([d.kind for d in result.diagnostics], ["uninferred"])


class TestScale(unittest.TestCase):
    def test_large_program_terminates(self):
        prog = build_chain(1500)
        self.assertGreater(count_nodes(prog), 10_000)
        start = time.perf_counter()
        result = check_program(prog)
        elapsed = time.perf_counter() - start
        self.assertEqual(result.format_main(), "int")
        self.assertLess(elapsed, 30.0)


if __name__ == "__main__":
    unittest.main()
