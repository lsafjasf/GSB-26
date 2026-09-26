"""性能基准：不同规模程序的类型检查耗时。

运行：python3 bench.py
"""
import sys
import time

sys.setrecursionlimit(1_000_000)

from typechecker.astnodes import count_nodes
from typechecker.infer import Checker
from typechecker.parser import parse_program


def gen_def_chain(n: int) -> str:
    """n 个函数定义，每个引用前一个两次（调用图是一条长链）。"""
    lines = ["(def f0 (fn x (+ x 1)))"]
    for i in range(1, n):
        lines.append(f"(def f{i} (fn x (f{i - 1} (f{i - 1} x))))")
    lines.append("(def result (f{} 0))".format(n - 1))
    return "\n".join(lines)


def gen_mutual_recursion(groups: int, size: int) -> str:
    """groups 个互递归组，每组 size 个函数成环。"""
    parts = []
    for g in range(groups):
        bindings = []
        for i in range(size):
            nxt = g * size + (i + 1) % size
            bindings.append(
                f"  (g{g * size + i} (fn n (if (= n 0) 0 (g{nxt} (- n 1)))))")
        parts.append("(defrec\n" + "\n".join(bindings) + ")")
    return "\n".join(parts)


def gen_polymorphic_use(n: int) -> str:
    """一个多态函数被实例化 n 次。"""
    lines = ["(def id (fn x x))", "(def apply2 (fn f (fn x (f (f x)))))"]
    for i in range(n):
        lines.append(f"(def u{i} ((apply2 id) {i}))")
    return "\n".join(lines)


def gen_wide_exprs(n: int) -> str:
    """n 个独立的顶层表达式。"""
    return "\n".join(f"(+ {i} (* {i} 2))" for i in range(n))


def bench(name: str, src: str) -> None:
    t0 = time.perf_counter()
    program = parse_program(src)
    t1 = time.perf_counter()
    nodes = count_nodes(program)
    checker = Checker()
    t2 = time.perf_counter()
    result = checker.check(program)
    t3 = time.perf_counter()
    status = "ok" if result.ok else f"{len(result.errors)} 个错误"
    print(f"{name:<26} {nodes:>8}  {t1 - t0:>8.3f}s  {t3 - t2:>8.3f}s  "
          f"{t3 - t0:>8.3f}s  [{status}]")
    if not result.ok:
        raise SystemExit(f"基准程序应当无类型错误: {name}")


if __name__ == "__main__":
    print(f"{'场景':<26} {'节点':>8}  {'解析':>9}  {'检查':>9}  {'合计':>9}")
    bench("定义链 2k", gen_def_chain(2_000))
    bench("定义链 10k（约7万节点）", gen_def_chain(10_000))
    bench("定义链 20k（约14万节点）", gen_def_chain(20_000))
    bench("互递归 100组x50（约3万节点）", gen_mutual_recursion(100, 50))
    bench("多态实例化 5k", gen_polymorphic_use(5_000))
    bench("宽表达式 10k", gen_wide_exprs(10_000))
