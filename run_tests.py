#!/usr/bin/env python3
"""对拍 + 统计脚本。

用法: python3 run_tests.py

1. 对内置测试程序逐一：解释执行优化前/后版本，比较运行结果（状态 + 输出 + 陷阱）；
2. 随机程序模糊对拍（默认 300 个种子）；
3. 输出优化前后语句数与耗时统计表（打印并写入 stats.md）；
4. 导出删除记录样例（deletion_log_sample.json）。
"""
import json
import random
import time

from irfold.interp import run, signature
from irfold.ir import parse
from irfold.optimize import optimize
from irfold.programs import CASES

FUZZ_SEEDS = 300


def check_case(name, src, input_sets):
    """优化并对拍单个程序，返回统计行。"""
    prog = parse(src)
    t0 = time.perf_counter()
    opt, log, iters = optimize(prog)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    for inputs in input_sets:
        before = run(prog, inputs)
        after = run(opt, inputs)
        assert signature(before) == signature(after), (
            f"[{name}] 语义不一致 inputs={inputs}: "
            f"before={signature(before)} after={signature(after)}"
        )
    folds = sum(1 for e in log if e["action"] == "fold")
    prunes = sum(1 for e in log if e["action"] == "prune")
    removes = sum(1 for e in log if e["action"] == "remove")
    return {
        "name": name,
        "before": len(prog),
        "after": len(opt),
        "iterations": iters,
        "folds": folds,
        "prunes": prunes,
        "removes": removes,
        "time_ms": elapsed_ms,
        "log": log,
        "optimized": opt.dump(),
    }


def structural_asserts(results):
    """针对特定结构的断言：确保该删的删了、该留的留了。"""
    by_name = {r["name"]: r for r in results}

    nested = by_name["nested_branch"]["optimized"]
    assert "cjmp" not in nested, "嵌套常量分支应全部被裁剪"
    assert "print 50" in nested, "42 + 8 应被折叠为 50"

    all_unr = by_name["all_unreachable"]["optimized"]
    assert all_unr == "print 1\nhalt", f"全部不可达代码应被删尽, got:\n{all_unr}"

    self_jump = by_name["self_jump"]["optimized"]
    assert "jmp LOOP" in self_jump, "自跳转死循环必须保留"
    assert "halt" not in self_jump, "死循环后的 halt 不可达，应删除"

    div_zero = by_name["div_zero"]["optimized"]
    assert "div" in div_zero, "除零指令不得在编译期折叠或删除"

    loop_const = by_name["loop_const"]["optimized"]
    assert "print 21" in loop_const, "循环内常量 10*2+1 应折叠为 21"
    assert "cjmp cond" in loop_const, "循环条件依赖循环变量，不得裁剪"


# ------------------------------------------------------------- 随机模糊对拍

VARS = ["a", "b", "c", "d"]
BINOPS = ["add", "sub", "mul", "div", "mod", "lt", "le", "gt", "ge", "eq", "ne"]


def gen_program(rng):
    """生成只含前向跳转的随机程序（保证终止），可能包含除零陷阱。"""
    lines = []
    pending = []
    counter = [0]

    def new_label():
        counter[0] += 1
        return f"L{counter[0]}"

    def operand():
        if rng.random() < 0.6:
            return rng.choice(VARS)
        return str(rng.randint(-20, 20))

    for _ in range(rng.randint(10, 30)):
        while pending and rng.random() < 0.5:
            lines.append(f"{pending.pop()}:")
        r = rng.random()
        if r < 0.30:
            lines.append(f"const {rng.choice(VARS)}, {rng.randint(-100, 100)}")
        elif r < 0.55:
            lines.append(
                f"binop {rng.choice(VARS)}, {rng.choice(BINOPS)}, "
                f"{operand()}, {operand()}"
            )
        elif r < 0.65:
            lines.append(f"mov {rng.choice(VARS)}, {operand()}")
        elif r < 0.75:
            lines.append(f"print {operand()}")
        elif r < 0.85:
            l1, l2 = new_label(), new_label()
            pending += [l1, l2]
            lines.append(f"cjmp {operand()}, {l1}, {l2}")
        elif r < 0.92:
            lines.append(f"input {rng.choice(VARS)}")
        else:
            lbl = new_label()
            pending.append(lbl)
            lines.append(f"jmp {lbl}")
    while pending:
        lines.append(f"{pending.pop()}:")
    lines.append("halt")
    return "\n".join(lines)


def fuzz():
    rng = random.Random(20260927)
    for seed in range(FUZZ_SEEDS):
        src = gen_program(rng)
        prog = parse(src)
        opt, _, _ = optimize(prog)
        inputs = [rng.randint(-2**63, 2**63 - 1) for _ in range(8)]
        before = run(prog, inputs)
        after = run(opt, inputs)
        assert signature(before) == signature(after), (
            f"fuzz seed {seed} 语义不一致:\n{src}\n--- 优化后 ---\n{opt.dump()}\n"
            f"before={signature(before)} after={signature(after)}"
        )
    print(f"fuzz: {FUZZ_SEEDS} 个随机程序对拍通过")


# ------------------------------------------------------------- 主流程

def main():
    results = []
    for name, src, input_sets in CASES:
        results.append(check_case(name, src, input_sets))
        print(f"case {name}: 对拍通过 ({len(input_sets)} 组输入)")

    structural_asserts(results)
    print("structural: 结构断言全部通过")

    fuzz()

    header = (
        "| 程序 | 优化前语句数 | 优化后语句数 | 迭代轮数 | 折叠 | 分支裁剪 | 删除 | 耗时(ms) |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {r['name']} | {r['before']} | {r['after']} | {r['iterations']} "
        f"| {r['folds']} | {r['prunes']} | {r['removes']} | {r['time_ms']:.2f} |"
        for r in results
    ]
    table = header + "\n" + "\n".join(rows)
    print("\n" + table)

    with open("stats.md", "w", encoding="utf-8") as f:
        f.write("# 优化统计\n\n" + table + "\n")

    sample = {r["name"]: r["log"] for r in results if r["log"]}
    with open("deletion_log_sample.json", "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    print("\n已写出 stats.md 与 deletion_log_sample.json")


if __name__ == "__main__":
    main()
