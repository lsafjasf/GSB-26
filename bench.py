"""Performance smoke test: type-check large programs (10k+ AST nodes).

Run:  python3 bench.py
"""
import time

from typecheck import (Binding, BoolLit, Call, If, IntLit, Lam, Pos, Program,
                       Var, check_program)
from typecheck.ast_nodes import Node


def v(name):
    return Var(Pos(), name)


def i(value):
    return IntLit(Pos(), value)


def lam(param, body):
    return Lam(Pos(), param, body)


def app(fn, *args):
    node = fn
    for arg in args:
        node = Call(Pos(), node, arg)
    return node


def count_nodes(node):
    if node is None:
        return 0
    if isinstance(node, Program):
        return sum(count_nodes(b.value) for g in node.groups for b in g) \
            + count_nodes(node.main)
    total = 1
    for value in vars(node).values():
        if isinstance(value, Node):
            total += count_nodes(value)
    return total


def build_chain(n):
    """Deep dependency chain: f0 = \\x. add x 1 ; fk = \\x. f(k-1) (add x 1)."""
    groups = []
    prev = None
    for k in range(n):
        if k == 0:
            value = lam("x", app(v("add"), v("x"), i(1)))
        else:
            value = lam("x", Call(Pos(), v(prev), app(v("add"), v("x"), i(1))))
        groups.append([Binding(f"f{k}", value)])
        prev = f"f{k}"
    return Program(groups=groups, main=Call(Pos(), v(prev), i(0)))


def build_poly(n):
    """Heavy let-polymorphism: id is generalised once, instantiated 3n times."""
    groups = [[Binding("id", lam("x", v("x")))]]
    prev = "id"
    for k in range(n):
        value = lam("x", Call(Pos(), v(prev), app(v("id"), app(v("id"), v("x")))))
        groups.append([Binding(f"k{k}", value)])
        prev = f"k{k}"
    return Program(groups=groups, main=Call(Pos(), v(prev), i(0)))


def build_mutual(n):
    """n/2 mutually recursive groups of two functions each."""
    groups = []
    for k in range(n // 2):
        even = lam("x", If(Pos(), app(v("eq"), v("x"), i(0)), BoolLit(Pos(), True),
                           Call(Pos(), v(f"odd{k}"),
                                app(v("sub"), v("x"), i(1)))))
        odd = lam("x", If(Pos(), app(v("eq"), v("x"), i(0)), BoolLit(Pos(), False),
                          Call(Pos(), v(f"even{k}"),
                               app(v("sub"), v("x"), i(1)))))
        groups.append([Binding(f"even{k}", even), Binding(f"odd{k}", odd)])
    return Program(groups=groups, main=Call(Pos(), v(f"even{n // 2 - 1}"), i(10)))


def run(name, prog):
    nodes = count_nodes(prog)
    start = time.perf_counter()
    result = check_program(prog)
    elapsed = time.perf_counter() - start
    rate = nodes / elapsed if elapsed else float("inf")
    print(f"{name:<28} nodes={nodes:>7}  defs={len(prog.groups):>5}  "
          f"constraints={result.constraint_count:>6}  "
          f"steps={result.solver_steps:>7}  "
          f"time={elapsed * 1000:8.1f} ms  ({rate:,.0f} nodes/s)")
    assert result.main_type is not None


if __name__ == "__main__":
    run("chain (deep deps)", build_chain(2500))
    run("polymorphic instantiations", build_poly(2500))
    run("mutual recursion groups", build_mutual(2000))
    run("chain x2 (20k+ nodes)", build_chain(5000))
