"""基准对比：重构前后在典型程序上的耗时 + 代码结构统计。

用法: python3 bench.py
"""

import ast
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vm"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))

import legacy_vm
import table_vm
from programs import asm

REPEAT = 5


def sum_loop(n):
    return asm(
        ("push", 0), ("store", "acc"),
        ("push", 1), ("store", "i"),
        (":", "loop"),
        ("load", "i"), ("push", n), ("le",), ("jz", "end"),
        ("load", "acc"), ("load", "i"), ("add",), ("store", "acc"),
        ("load", "i"), ("push", 1), ("add",), ("store", "i"),
        ("jmp", "loop"),
        (":", "end"),
        ("load", "acc"), ("halt",))


def fib_iter(n):
    return asm(
        ("push", 0), ("store", "a"),
        ("push", 1), ("store", "b"),
        ("push", 0), ("store", "i"),
        (":", "loop"),
        ("load", "i"), ("push", n), ("lt",), ("jz", "end"),
        ("load", "a"), ("load", "b"), ("add",), ("store", "t"),
        ("load", "b"), ("store", "a"),
        ("load", "t"), ("store", "b"),
        ("load", "i"), ("push", 1), ("add",), ("store", "i"),
        ("jmp", "loop"),
        (":", "end"),
        ("load", "a"), ("push", 10**6), ("mod",), ("halt",))


def throw_loop(n):
    return asm(
        ("push", 0), ("store", "cnt"),
        (":", "loop"),
        ("load", "cnt"), ("push", n), ("lt",), ("jz", "end"),
        ("try", "h"),
        ("push", 1), ("throw",),
        (":", "h"),
        ("load", "cnt"), ("add",), ("store", "cnt"),
        ("jmp", "loop"),
        (":", "end"),
        ("load", "cnt"), ("halt",))


BENCH_PROGRAMS = [
    ("sum_loop(20000)", sum_loop(20000)),
    ("fib_iter(20000)", fib_iter(20000)),
    ("throw_loop(5000)", throw_loop(5000)),
]


def timeit(run, program):
    best = float("inf")
    result = None
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        result = run(program)
        best = min(best, time.perf_counter() - t0)
    return best, result


def structure_stats(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    longest = max((f.end_lineno - f.lineno + 1, f.name) for f in funcs)
    loc = sum(1 for line in src.splitlines()
              if line.strip() and not line.strip().startswith("#"))
    return {
        "loc": loc,
        "functions": len(funcs),
        "longest_func": longest,
        "dispatch_branches": src.count("elif op ==") + (1 if "if op ==" in src else 0),
        "dispatch_table": src.count("@instruction("),
    }


def main():
    print("=== 耗时对比（5 次取最优，单位 ms）===")
    print(f"{'程序':<20}{'重构前':>10}{'重构后':>10}{'比值':>8}")
    for name, prog in BENCH_PROGRAMS:
        t_before, r_before = timeit(legacy_vm.run, prog)
        t_after, r_after = timeit(table_vm.run, prog)
        assert r_before == r_after, f"{name}: 结果不一致 {r_before} != {r_after}"
        print(f"{name:<20}{t_before*1e3:>10.1f}{t_after*1e3:>10.1f}"
              f"{t_after/t_before:>8.2f}x   (steps={r_before.steps})")

    print("\n=== 代码结构对比 ===")
    base = os.path.dirname(os.path.abspath(__file__))
    stats = {}
    for label, rel in [("重构前 legacy_vm.py", "vm/legacy_vm.py"),
                       ("重构后 table_vm.py", "vm/table_vm.py")]:
        stats[label] = structure_stats(os.path.join(base, rel))
    print(f"{'指标':<24}{'重构前':>10}{'重构后':>10}")
    rows = [
        ("有效代码行数", "loc"),
        ("函数数量", "functions"),
        ("最长函数行数", None),
        ("分发分支数(elif)", "dispatch_branches"),
        ("指令表注册数", "dispatch_table"),
    ]
    for label, key in rows:
        if key is None:
            b = stats["重构前 legacy_vm.py"]["longest_func"][0]
            a = stats["重构后 table_vm.py"]["longest_func"][0]
        else:
            b = stats["重构前 legacy_vm.py"][key]
            a = stats["重构后 table_vm.py"][key]
        print(f"{label:<24}{b:>10}{a:>10}")
    longest_after = stats["重构后 table_vm.py"]["longest_func"]
    print(f"\n重构后最长函数: {longest_after[1]}() 共 {longest_after[0]} 行（主循环）")
    print("新增一条指令: 重构前需在巨型 if/elif 链中加分支并操作共享局部变量；")
    print("              重构后只需一处实现(op_xxx) + 一处注册(@instruction)。")


if __name__ == "__main__":
    main()
