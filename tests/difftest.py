"""逐指令对拍测试：同一批程序 + 输入，同时跑重构前后两个解释器，
比较执行结果（值/输出）、错误类型、执行步数，必须完全一致。

用法: python3 tests/difftest.py [-v]
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vm"))
sys.path.insert(0, os.path.dirname(__file__))

import legacy_vm
import table_vm
from programs import CASES

FUZZ_COUNT = 500
FUZZ_SEED = 20260927
FUZZ_STEP_LIMIT = 2000

NO_ARG_OPS = ["add", "sub", "mul", "div", "mod", "neg", "eq", "ne", "lt", "le",
              "gt", "ge", "not", "dup", "drop", "swap", "endtry", "throw",
              "print", "input", "halt"]
VAR_NAMES = ["a", "b", "i", "acc"]


def fuzz_programs(rng, count):
    for _ in range(count):
        n = rng.randint(1, 40)
        prog = []
        for _ in range(n):
            kind = rng.random()
            if kind < 0.25:
                prog.append(("push", rng.choice(
                    [rng.randint(-20, 20), rng.choice(["x", "ab", ""]), True, False])))
            elif kind < 0.35:
                prog.append((rng.choice(["load", "store"]), rng.choice(VAR_NAMES)))
            elif kind < 0.50:
                prog.append((rng.choice(["jmp", "jz", "try"]),
                             rng.randint(-2, n + 3)))  # 含越界地址
            elif kind < 0.55:
                prog.append((rng.choice(["wat", "NOP", ""]), None))
            else:
                prog.append((rng.choice(NO_ARG_OPS),))
        inputs = tuple(rng.randint(-10, 10) for _ in range(rng.randint(0, 4)))
        yield prog, inputs


def run_both(program, inputs, step_limit):
    kwargs = {} if step_limit is None else {"step_limit": step_limit}
    before = legacy_vm.run(program, inputs, **kwargs)
    after = table_vm.run(program, inputs, **kwargs)
    return before, after


def check(name, program, inputs, step_limit=None, verbose=False):
    before, after = run_both(program, inputs, step_limit)
    ok = before == after
    if verbose or not ok:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name}: before={before} after={after}")
    return ok


def main():
    verbose = "-v" in sys.argv
    total = failed = 0

    for case in CASES:
        total += 1
        if not check(case.name, case.program, case.inputs, case.step_limit, verbose):
            failed += 1

    rng = random.Random(FUZZ_SEED)
    for i, (prog, inputs) in enumerate(fuzz_programs(rng, FUZZ_COUNT)):
        total += 1
        if not check(f"fuzz#{i}", prog, inputs, FUZZ_STEP_LIMIT, verbose):
            failed += 1

    print(f"\n对拍完成: {total - failed}/{total} 通过"
          f"（手工用例 {len(CASES)} 条 + 随机用例 {FUZZ_COUNT} 条）")
    if failed:
        print(f"有 {failed} 条不一致！")
        sys.exit(1)
    print("重构前后行为完全一致（结果值 / 输出 / 错误类型 / 执行步数）。")


if __name__ == "__main__":
    main()
