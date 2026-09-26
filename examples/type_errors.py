"""Sample type errors, rendered exactly as the checker reports them.

Run:  python3 examples/type_errors.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typecheck import (Binding, BoolLit, Call, If, IntLit, Lam, Let, Pos,
                       Program, TypeCheckError, Var, check_program)


def v(name, line=0, col=0):
    return Var(Pos(line, col), name)


def i(value, line=0, col=0):
    return IntLit(Pos(line, col), value)


def lam(param, body, line=0, col=0):
    return Lam(Pos(line, col), param, body)


def app(fn, *args, line=0, col=0):
    node = fn
    for arg in args:
        node = Call(Pos(line, col), node, arg)
    return node


def show(title, prog):
    print(f"=== {title} ===")
    try:
        result = check_program(prog)
    except TypeCheckError as err:
        print(err.diagnostic.render())
    else:
        for diag in result.diagnostics:
            print(diag.render())
        if not result.diagnostics:
            print(f"(no error; main : {result.format_main()})")
    print()


# 1. Argument type mismatch: add 1 true
show("add 1 true",
     Program(main=app(v("add", 1, 1), i(1, 1, 5), BoolLit(Pos(1, 11), True),
                      line=1, col=7)))

# 2. Conflict propagated through a polymorphic function:
#    let apply = \f. \x. f x in apply (\x. add x 1) true
apply_fn = lam("f", lam("x", app(v("f"), v("x"))))
show("apply (\\x. add x 1) true",
     Program(main=Let(Pos(), "apply", apply_fn,
                      app(v("apply", 3, 1), lam("x", app(v("add"), v("x"), i(1))),
                          BoolLit(Pos(3, 20), True), line=3, col=9))))

# 3. Infinite type via self-application: \x. x x
show("\\x. x x  (occurs check)",
     Program(main=lam("x", Call(Pos(5, 8), v("x"), v("x")), line=5, col=1)))

# 4. Mutually recursive group with a conflict inside:
#    even = \n. if eq n 0 then true else odd (sub n 1)
#    odd  = \n. if eq n 0 then 1    else even (sub n 1)   -- 1 is not bool
even = lam("n", If(Pos(), app(v("eq"), v("n"), i(0)), BoolLit(Pos(), True),
                   Call(Pos(), v("odd"), app(v("sub"), v("n"), i(1)))))
odd = lam("n", If(Pos(9, 12), app(v("eq"), v("n"), i(0)), i(1, 9, 30),
                  Call(Pos(), v("even"), app(v("sub"), v("n"), i(1)))))
show("mutual recursion with bad branch",
     Program(groups=[[Binding("even", even), Binding("odd", odd)]],
             main=Call(Pos(), v("even"), i(10))))

# 5. Uninferable parameter: \x. 1
show("\\x. 1  (uninferred parameter)",
     Program(main=lam("x", i(1), line=7, col=1)))
