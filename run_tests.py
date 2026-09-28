#!/usr/bin/env python3
"""对拍 + 统计脚本。

用法: python3 run_tests.py

1. 对内置测试程序逐一：解释执行优化前/后版本，比较运行结果（状态 + 输出 + 陷阱）；
2. 随机程序模糊对拍（默认 300 个种子）；生成器产生带标签与回边的程序
   （回边只用于必然终止的有界计数循环），并断言回边确实被生成、被优化保留；
3. 输出优化前后语句数与耗时统计表（打印并写入 stats.md）；
4. 导出删除记录样例（deletion_log_sample.json）。

等价判据见 ``irfold.interp.equivalent``：双方均命中步数上限时按输出前缀比较。
"""
import json
import random
import time

from irfold.interp import equivalent, run, signature
from irfold.ir import parse, successors
from irfold.optimize import optimize
from irfold.programs import BOTH_TIMEOUT_PREFIX, CASES

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
        assert equivalent(before, after), (
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

    nested_loop = by_name["nested_loop_const"]["optimized"]
    assert "print 420" in nested_loop, "嵌套循环内常量 20*21 应折叠为 420"
    assert "INNER" in nested_loop and "OUTER" in nested_loop, (
        "嵌套循环的回边标签必须保留"
    )

    # 守卫“双方均超时按输出前缀比较”的约定：两侧都 step_limit、输出条数不同，
    # 严格签名不等价，但按输出前缀应判等价。
    btp_src = parse(BOTH_TIMEOUT_PREFIX)
    btp_opt, _, _ = optimize(btp_src)
    btp_before, btp_after = run(btp_src), run(btp_opt)
    assert btp_before["status"] == "step_limit" == btp_after["status"]
    assert len(btp_before["outputs"]) != len(btp_after["outputs"]), (
        "折叠应缩短循环体，使两侧超时输出条数不同"
    )
    assert signature(btp_before) != signature(btp_after)
    assert equivalent(btp_before, btp_after)


# ------------------------------------------------------------- 随机模糊对拍

VARS = ["a", "b", "c", "d"]
COUNTERS = ["i", "j"]  # 循环计数器保留变量名，直线代码不得改写，保证循环必然终止
BINOPS = ["add", "sub", "mul", "div", "mod", "lt", "le", "gt", "ge", "eq", "ne"]


def gen_program(rng):
    """生成带标签与回边的随机程序，必然终止，可能包含除零陷阱。

    程序由若干段组成，每段要么是一段直线代码（可插前向 ``cjmp``/``jmp``，
    标签统一在段尾落地，因此这些跳转只前向），要么是一个有界计数循环：

      const h, 0
      HEAD: <循环体：直线代码，可嵌套循环，不写计数器 h>
             binop h, add, h, 1
             cjmp h, HEAD, DONE
      DONE:

    回边（``HEAD``/``DONE``）只跳回循环头；循环上界是字面量常量、计数器在
    循环体内不被修改，故循环必然终止、迭代次数固定，模糊对拍不会意外超时。
    """
    lines = []
    pending = []
    counter = [0]
    depth = [0]

    def new_label():
        counter[0] += 1
        return f"L{counter[0]}"

    def operand():
        if rng.random() < 0.6:
            return rng.choice(VARS)
        return str(rng.randint(-20, 20))

    def straight_line():
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
        elif r < 0.90:
            lines.append(f"input {rng.choice(VARS)}")
        else:
            lbl = new_label()
            pending.append(lbl)
            lines.append(f"jmp {lbl}")  # 标签在段尾落地：只前向

    def flush_pending():
        while pending:
            lines.append(f"{pending.pop()}:")

    def loop(depth_left):
        """生成一个有界计数循环。

        进入前先把外层攒着的前向标签落地；循环体内产生的前向标签会在
        本循环的 ``done`` 标签之前落地，不会跨循环逃逸到错误的别名目标。
        """
        flush_pending()
        head, done = new_label(), new_label()
        cnt = COUNTERS[min(depth[0], len(COUNTERS) - 1)]
        bound = rng.randint(2, 6)
        lines.append(f"const {cnt}, 0")
        lines.append(f"{head}:")
        for _ in range(rng.randint(2, 5)):
            if depth_left > 0 and rng.random() < 0.35:
                depth[0] += 1
                loop(depth_left - 1)
                depth[0] -= 1
            else:
                straight_line()
        lines.append(f"binop {cnt}, add, {cnt}, 1")
        lines.append(f"binop {cnt}cond, lt, {cnt}, {bound}")
        lines.append(f"cjmp {cnt}cond, {head}, {done}")
        lines.append(f"{done}:")
        flush_pending()

    for _ in range(rng.randint(3, 6)):
        flush_pending()
        if rng.random() < 0.45:
            loop(depth_left=1)  # 最多两层嵌套循环
        else:
            for _ in range(rng.randint(1, 4)):
                if rng.random() < 0.2:
                    l1, l2 = new_label(), new_label()
                    pending += [l1, l2]
                    lines.append(f"cjmp {operand()}, {l1}, {l2}")
                else:
                    straight_line()
    flush_pending()
    lines.append("halt")
    return "\n".join(lines)


def back_edges(prog):
    """统计 CFG 中的回边数（目标下标不大于跳转指令自身）。"""
    succs = successors(prog)
    return sum(1 for i, ts in enumerate(succs) for t in ts if t <= i)


def fuzz():
    rng = random.Random(20260927)
    programs_with_backedge = 0
    back_edges_before = 0
    both_timeout = 0
    for seed in range(FUZZ_SEEDS):
        src = gen_program(rng)
        prog = parse(src)
        opt, _, _ = optimize(prog)
        be_before, be_after = back_edges(prog), back_edges(opt)
        if be_after:
            programs_with_backedge += 1
        back_edges_before += be_before
        inputs = [rng.randint(-2**63, 2**63 - 1) for _ in range(8)]
        before = run(prog, inputs)
        after = run(opt, inputs)
        assert equivalent(before, after), (
            f"fuzz seed {seed} 语义不一致:\n{src}\n--- 优化后 ---\n{opt.dump()}\n"
            f"before={signature(before)} after={signature(after)}"
        )
        if before["status"] == "step_limit" and after["status"] == "step_limit":
            both_timeout += 1
    assert programs_with_backedge >= FUZZ_SEEDS // 2, (
        f"300 个随机程序中仅 {programs_with_backedge} 个优化后仍含回边，回边覆盖不足"
    )
    print(
        f"fuzz: {FUZZ_SEEDS} 个随机程序对拍通过"
        f"（{programs_with_backedge} 个优化后仍含回边；"
        f"生成器回边总数 {back_edges_before}，"
        f"双方均超时 {both_timeout} 个）"
    )


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
