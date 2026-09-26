"""测试程序语料库：手工用例 + 迷你汇编器（标签解析）。"""

from collections import namedtuple

Case = namedtuple("Case", "name program inputs step_limit", defaults=[(), None])

JUMP_OPS = ("jmp", "jz", "try")


def asm(*lines):
    """把 (':', label) 标签解析成地址；jmp/jz/try 的字符串操作数视为标签。"""
    labels = {}
    program = []
    for line in lines:
        if line[0] == ":":
            labels[line[1]] = len(program)
        else:
            program.append(list(line))
    for instr in program:
        if instr[0] in JUMP_OPS and len(instr) > 1 and isinstance(instr[1], str):
            instr[1] = labels[instr[1]]
    return [tuple(i) for i in program]


CASES = [
    # --- 算术 / 栈操作 ---
    Case("arith", asm(("push", 3), ("push", 4), ("add",), ("push", 2), ("mul",), ("halt",))),
    Case("neg_mod", asm(("push", 7), ("neg",), ("push", 3), ("mod",), ("halt",))),
    Case("div_floor", asm(("push", -7), ("push", 2), ("div",), ("halt",))),
    Case("dup_drop_swap", asm(
        ("push", 1), ("push", 2), ("swap",), ("push", 3), ("drop",),
        ("add",), ("dup",), ("add",), ("halt",))),
    Case("strings", asm(
        ("push", "foo"), ("push", "bar"), ("add",), ("dup",), ("print",),
        ("push", "foobar"), ("eq",), ("halt",))),
    Case("comparisons", asm(
        ("push", 3), ("push", 4), ("lt",), ("jz", "a"),
        ("push", 1), ("jmp", "b"),
        (":", "a"), ("push", 0),
        (":", "b"), ("halt",))),
    Case("not_op", asm(("push", 0), ("not",), ("halt",))),

    # --- 变量 / 循环 ---
    Case("sum_loop", asm(
        ("push", 0), ("store", "acc"),
        ("push", 1), ("store", "i"),
        (":", "loop"),
        ("load", "i"), ("push", 10), ("le",), ("jz", "end"),
        ("load", "acc"), ("load", "i"), ("add",), ("store", "acc"),
        ("load", "i"), ("push", 1), ("add",), ("store", "i"),
        ("jmp", "loop"),
        (":", "end"),
        ("load", "acc"), ("halt",))),

    # --- 提前返回（halt 在分支内）---
    Case("early_return_neg", asm(
        ("input",), ("dup",), ("push", 0), ("lt",), ("jz", "pos"),
        ("drop",), ("push", -1), ("halt",),
        (":", "pos"), ("push", 100), ("add",), ("halt",)), (-3,)),
    Case("early_return_pos", asm(
        ("input",), ("dup",), ("push", 0), ("lt",), ("jz", "pos"),
        ("drop",), ("push", -1), ("halt",),
        (":", "pos"), ("push", 100), ("add",), ("halt",)), (5,)),

    # --- 无条件跳转（跳过会出错的死代码）---
    Case("jmp_over_dead_code", asm(
        ("push", 1), ("jmp", "skip"),
        ("push", 0), ("div",),
        (":", "skip"), ("push", 2), ("add",), ("halt",))),

    # --- 异常处理 ---
    Case("try_throw_caught", asm(
        ("try", "h"),
        ("push", 7), ("push", 42), ("throw",),
        ("endtry",), ("push", 0),
        (":", "h"), ("push", 100), ("add",), ("halt",))),
    Case("try_stack_restore", asm(
        ("push", 1), ("push", 2),
        ("try", "h"),
        ("push", 99), ("push", 98), ("push", 5), ("throw",),
        (":", "h"), ("add",), ("add",), ("halt",))),
    Case("nested_try", asm(
        ("try", "outer"),
        ("try", "inner"),
        ("push", 1), ("push", 0), ("div",),
        ("endtry",),
        (":", "inner"), ("drop",), ("push", 5), ("throw",),
        (":", "outer"), ("halt",))),
    Case("try_no_throw", asm(
        ("try", "h"), ("push", 8), ("endtry",), ("jmp", "end"),
        (":", "h"), ("push", 9),
        (":", "end"), ("halt",))),
    Case("throw_after_endtry", asm(
        ("try", "h"), ("endtry",), ("push", 1), ("throw",),
        (":", "h"), ("push", 0), ("halt",))),
    Case("uncaught_throw", asm(("push", 9), ("throw",))),
    Case("div0_caught", asm(
        ("try", "h"),
        ("push", 1), ("push", 0), ("div",),
        ("endtry",),
        (":", "h"), ("print",), ("push", 0), ("halt",))),
    Case("div0_uncaught", asm(("push", 1), ("push", 0), ("div",))),
    Case("mod0_uncaught", asm(("push", 1), ("push", 0), ("mod",))),
    Case("invalid_jump_caught", asm(
        ("try", "h"), ("jmp", 999),
        (":", "h"), ("drop",), ("push", 4), ("halt",))),

    # --- 栈不平衡 ---
    Case("imbalance_extra", asm(("push", 1), ("push", 2), ("halt",))),
    Case("imbalance_empty", asm(("halt",),)),
    Case("falloff_ok", asm(("push", 5),)),
    Case("falloff_imbalance", asm(("push", 5), ("push", 6))),

    # --- 其他错误类型 ---
    Case("invalid_jump", asm(("jmp", 999),)),
    Case("invalid_jump_neg", asm(("jmp", -1),)),
    Case("undefined_var", asm(("load", "x"),)),
    Case("underflow_add", asm(("push", 1), ("add",))),
    Case("underflow_print", asm(("print",),)),
    Case("type_error_sub", asm(("push", "a"), ("push", 1), ("sub",))),
    Case("type_error_mixed_add", asm(("push", "a"), ("push", 1), ("add",))),
    Case("type_error_bool_add", asm(("push", 1), ("push", 2), ("lt",), ("push", 1), ("add",))),
    Case("unknown_op", (("wat",),)),
    Case("input_exhausted", asm(("input",), ("input",)), (1,)),

    # --- IO ---
    Case("input_print", asm(
        ("input",), ("input",), ("add",), ("dup",), ("print",), ("halt",)), (3, 4)),

    # --- 步数上限 ---
    Case("infinite_loop", asm((":", "l"), ("jmp", "l")), (), 100),
    Case("step_limit_exact", asm(
        ("push", 1), ("push", 2), ("add",), ("halt",)), (), 4),
    Case("step_limit_short", asm(
        ("push", 1), ("push", 2), ("add",), ("halt",)), (), 2),
]
