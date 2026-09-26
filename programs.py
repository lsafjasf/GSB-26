"""共享测试程序集：重构前后两个解释器跑同一批程序与输入。

包含：
- asm(): 迷你汇编器，支持标签，便于书写带跳转的程序；
- INSTRUCTION_CASES: 逐指令对拍用例（每条指令至少一个）；
- INTEGRATION_CASES: 异常处理 / 提前返回 / 无条件跳转 / 栈不平衡等综合用例；
- FIB_PROGRAM / THROW_LOOP_PROGRAM: 基准测试程序；
- make_fuzz_cases(): 固定种子的随机程序，用于大规模对拍。
"""

import random


def asm(source):
    """把带标签的汇编文本编译成 [(op, arg), ...]。

    每行一条指令："OP arg"；标签单独一行："name:"；"#" 后是注释。
    arg 为整数、变量名或标签名。
    """
    instructions = []
    labels = {}
    for raw in source.strip().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith(":"):
            labels[line[:-1]] = len(instructions)
            continue
        parts = line.split(None, 1)
        op = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else None
        instructions.append([op, arg])
    code = []
    for op, arg in instructions:
        if isinstance(arg, str):
            if arg in labels:
                arg = labels[arg]
            elif arg.lstrip("-").isdigit():
                arg = int(arg)
        code.append((op, arg))
    return code


class Case:
    """一个对拍用例：程序 + 输入 + 步数上限 + 可选的期望结果。"""

    def __init__(self, name, program, inputs=(), max_steps=100000,
                 expect=None, opcodes=()):
        self.name = name
        self.program = program
        self.inputs = list(inputs)
        self.max_steps = max_steps
        self.expect = expect or {}
        self.opcodes = tuple(opcodes)   # 该用例覆盖的指令（用于覆盖率检查）


# ---------------------------------------------------------------------------
# 逐指令对拍用例
# ---------------------------------------------------------------------------

INSTRUCTION_CASES = [
    Case("op_push_halt", asm("""
        PUSH 42
        HALT
    """), expect={"status": "ok", "value": None, "steps": 2},
        opcodes=("PUSH", "HALT")),

    Case("op_store_load", asm("""
        PUSH 42
        STORE x
        LOAD x
        PRINT
        HALT
    """), expect={"status": "ok", "output": [42]},
        opcodes=("STORE", "LOAD")),

    Case("op_load_undefined", asm("""
        LOAD nope
        HALT
    """), expect={"status": "error", "error_kind": "UndefinedVariable"},
        opcodes=("LOAD",)),

    Case("op_arith", asm("""
        PUSH 6
        PUSH 3
        ADD
        PRINT
        PUSH 10
        PUSH 4
        SUB
        PRINT
        PUSH 6
        PUSH 7
        MUL
        PRINT
        PUSH 17
        PUSH 5
        DIV
        PRINT
        PUSH 17
        PUSH 5
        MOD
        PRINT
        PUSH 5
        NEG
        PRINT
        HALT
    """), expect={"status": "ok", "output": [9, 6, 42, 3, 2, -5]},
        opcodes=("ADD", "SUB", "MUL", "DIV", "MOD", "NEG", "PRINT")),

    Case("op_div_zero", asm("""
        PUSH 1
        PUSH 0
        DIV
        HALT
    """), expect={"status": "error", "error_kind": "DivisionByZero"},
        opcodes=("DIV",)),

    Case("op_mod_zero", asm("""
        PUSH 1
        PUSH 0
        MOD
        HALT
    """), expect={"status": "error", "error_kind": "DivisionByZero"},
        opcodes=("MOD",)),

    Case("op_add_underflow", asm("""
        PUSH 1
        ADD
        HALT
    """), expect={"status": "error", "error_kind": "StackUnderflow"},
        opcodes=("ADD",)),

    Case("op_compare", asm("""
        PUSH 3
        PUSH 4
        LT
        PRINT
        PUSH 3
        PUSH 4
        GT
        PRINT
        PUSH 4
        PUSH 4
        EQ
        PRINT
        PUSH 4
        PUSH 4
        NE
        PRINT
        PUSH 4
        PUSH 4
        LE
        PRINT
        PUSH 5
        PUSH 4
        GE
        PRINT
        HALT
    """), expect={"status": "ok", "output": [1, 0, 1, 0, 1, 1]},
        opcodes=("LT", "GT", "EQ", "NE", "LE", "GE")),

    Case("op_dup_drop_swap", asm("""
        PUSH 5
        DUP
        ADD
        PRINT
        PUSH 1
        PUSH 2
        SWAP
        PRINT
        PRINT
        PUSH 9
        DROP
        HALT
    """), expect={"status": "ok", "output": [10, 1, 2]},
        opcodes=("DUP", "SWAP", "DROP")),

    Case("op_jump", asm("""
        JUMP skip
        PUSH 1
        PRINT
        skip:
        PUSH 2
        PRINT
        HALT
    """), expect={"status": "ok", "output": [2]}, opcodes=("JUMP",)),

    Case("op_jump_invalid", asm("""
        JUMP 100
        HALT
    """), expect={"status": "error", "error_kind": "InvalidJump"},
        opcodes=("JUMP",)),

    Case("op_jump_negative", asm("""
        JUMP -1
        HALT
    """), expect={"status": "error", "error_kind": "InvalidJump"},
        opcodes=("JUMP",)),

    Case("op_jz_taken", asm("""
        PUSH 0
        JZ target
        PUSH 1
        PRINT
        target:
        PUSH 2
        PRINT
        HALT
    """), expect={"status": "ok", "output": [2]}, opcodes=("JZ",)),

    Case("op_jz_not_taken", asm("""
        PUSH 7
        JZ target
        PUSH 1
        PRINT
        target:
        HALT
    """), expect={"status": "ok", "output": [1]}, opcodes=("JZ",)),

    Case("op_jnz_taken", asm("""
        PUSH 3
        JNZ target
        PUSH 1
        PRINT
        target:
        PUSH 2
        PRINT
        HALT
    """), expect={"status": "ok", "output": [2]}, opcodes=("JNZ",)),

    Case("op_jnz_not_taken", asm("""
        PUSH 0
        JNZ target
        PUSH 1
        PRINT
        target:
        HALT
    """), expect={"status": "ok", "output": [1]}, opcodes=("JNZ",)),

    Case("op_try_throw_caught", asm("""
        TRY catch
        PUSH 42
        THROW
        catch:
        PRINT
        HALT
    """), expect={"status": "ok", "output": [42]},
        opcodes=("TRY", "THROW")),

    Case("op_throw_uncaught", asm("""
        PUSH 7
        THROW
        HALT
    """), expect={"status": "error", "error_kind": "UnhandledException"},
        opcodes=("THROW",)),

    Case("op_endtry", asm("""
        TRY catch
        PUSH 7
        ENDTRY
        PRINT
        HALT
        catch:
        PUSH 99
        PRINT
        HALT
    """), expect={"status": "ok", "output": [7]},
        opcodes=("TRY", "ENDTRY")),

    Case("op_endtry_underflow", asm("""
        ENDTRY
        HALT
    """), expect={"status": "error", "error_kind": "HandlerUnderflow"},
        opcodes=("ENDTRY",)),

    Case("op_try_invalid_target", asm("""
        TRY 100
        HALT
    """), expect={"status": "error", "error_kind": "InvalidJump"},
        opcodes=("TRY",)),

    Case("op_assert_depth_ok", asm("""
        PUSH 1
        PUSH 2
        ASSERT_DEPTH 2
        HALT
    """), expect={"status": "ok"}, opcodes=("ASSERT_DEPTH",)),

    Case("op_assert_depth_imbalance", asm("""
        PUSH 1
        PUSH 2
        ASSERT_DEPTH 1
        HALT
    """), expect={"status": "error", "error_kind": "StackImbalance"},
        opcodes=("ASSERT_DEPTH",)),

    Case("op_read", asm("""
        READ
        READ
        ADD
        PRINT
        HALT
    """), inputs=[3, 4], expect={"status": "ok", "output": [7]},
        opcodes=("READ",)),

    Case("op_read_exhausted", asm("""
        READ
        READ
        HALT
    """), inputs=[1], expect={"status": "error",
                              "error_kind": "InputExhausted"},
        opcodes=("READ",)),

    Case("op_return", asm("""
        PUSH 99
        RETURN
    """), expect={"status": "ok", "value": 99}, opcodes=("RETURN",)),

    Case("op_return_underflow", asm("""
        RETURN
    """), expect={"status": "error", "error_kind": "StackUnderflow"},
        opcodes=("RETURN",)),

    Case("op_unknown", [("FOOBAR", None)],
         expect={"status": "error", "error_kind": "UnknownOpcode"}),

    Case("op_fall_off_end", [("PUSH", 1)],
         expect={"status": "error", "error_kind": "UnexpectedEnd"}),

    Case("op_step_limit", [("JUMP", 0)], max_steps=1000,
         expect={"status": "error", "error_kind": "StepLimitExceeded",
                 "steps": 1001}),
]


# ---------------------------------------------------------------------------
# 综合用例：异常处理 / 提前返回 / 无条件跳转 / 栈不平衡
# ---------------------------------------------------------------------------

INTEGRATION_CASES = [
    Case("sum_loop", asm("""
        PUSH 0
        STORE sum
        PUSH 1
        STORE i
        loop:
        LOAD i
        PUSH 10
        LE
        JZ done
        LOAD sum
        LOAD i
        ADD
        STORE sum
        LOAD i
        PUSH 1
        ADD
        STORE i
        JUMP loop
        done:
        LOAD sum
        PRINT
        HALT
    """), expect={"status": "ok", "output": [55]}),

    Case("early_return", asm("""
        PUSH 0
        STORE i
        loop:
        LOAD i
        PUSH 5
        EQ
        JZ cont
        LOAD i
        RETURN
        cont:
        LOAD i
        PUSH 1
        ADD
        STORE i
        JUMP loop
    """), expect={"status": "ok", "value": 5}),

    Case("nested_try", asm("""
        TRY outer
        TRY inner
        PUSH 11
        THROW
        inner:
        PRINT
        PUSH 22
        THROW
        outer:
        PRINT
        HALT
    """), expect={"status": "ok", "output": [11, 22]}),

    Case("rethrow", asm("""
        TRY outer
        TRY inner
        PUSH 1
        THROW
        inner:
        PUSH 2
        THROW
        outer:
        PRINT
        HALT
    """), expect={"status": "ok", "output": [2]}),

    Case("try_restores_stack", asm("""
        PUSH 5
        TRY catch
        PUSH 1
        PUSH 2
        PUSH 3
        THROW
        catch:
        ASSERT_DEPTH 2
        PRINT
        PRINT
        HALT
    """), expect={"status": "ok", "output": [3, 5]}),

    Case("vm_error_not_caught_by_try", asm("""
        TRY catch
        PUSH 1
        PUSH 0
        DIV
        catch:
        HALT
    """), expect={"status": "error", "error_kind": "DivisionByZero"}),

    Case("throw_after_endtry_uncaught", asm("""
        TRY catch
        ENDTRY
        PUSH 1
        THROW
        catch:
        HALT
    """), expect={"status": "error", "error_kind": "UnhandledException"}),

    Case("jump_over_try_body", asm("""
        JUMP main
        handler:
        PRINT
        HALT
        main:
        TRY handler
        PUSH 8
        THROW
        HALT
    """), expect={"status": "ok", "output": [8]}),

    Case("return_inside_try", asm("""
        TRY catch
        PUSH 77
        RETURN
        catch:
        PUSH 1
        PRINT
        HALT
    """), expect={"status": "ok", "value": 77}),

    Case("countdown", asm("""
        PUSH 3
        STORE i
        loop:
        LOAD i
        JZ done
        LOAD i
        PRINT
        LOAD i
        PUSH 1
        SUB
        STORE i
        JUMP loop
        done:
        HALT
    """), expect={"status": "ok", "output": [3, 2, 1]}),
]


# ---------------------------------------------------------------------------
# 基准测试程序
# ---------------------------------------------------------------------------

FIB_PROGRAM = asm("""
    PUSH 0
    STORE a
    PUSH 1
    STORE b
    PUSH 0
    STORE i
    loop:
    LOAD i
    PUSH 25
    LT
    JZ done
    LOAD a
    LOAD b
    ADD
    STORE t
    LOAD b
    STORE a
    LOAD t
    STORE b
    LOAD i
    PUSH 1
    ADD
    STORE i
    JUMP loop
    done:
    LOAD a
    PRINT
    HALT
""")

THROW_LOOP_PROGRAM = asm("""
    PUSH 0
    STORE i
    loop:
    LOAD i
    PUSH 200
    LT
    JZ done
    TRY catch
    PUSH 1
    THROW
    catch:
    PUSH 9
    DROP
    LOAD i
    PUSH 1
    ADD
    STORE i
    JUMP loop
    done:
    LOAD i
    PRINT
    HALT
""")


# ---------------------------------------------------------------------------
# 随机对拍程序（固定种子，结果可复现）
# ---------------------------------------------------------------------------

_FUZZ_OPS = ("PUSH", "LOAD", "STORE", "ADD", "SUB", "MUL", "DIV", "MOD",
             "NEG", "EQ", "NE", "LT", "LE", "GT", "GE", "DUP", "DROP",
             "SWAP", "JUMP", "JZ", "JNZ", "TRY", "ENDTRY", "THROW",
             "ASSERT_DEPTH", "READ", "PRINT", "RETURN", "HALT")


def make_fuzz_cases(count=300, seed=20260927, max_len=40):
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        prog = []
        for _ in range(rng.randint(1, max_len)):
            op = rng.choice(_FUZZ_OPS)
            if op == "PUSH":
                arg = rng.randint(-20, 20)
            elif op in ("LOAD", "STORE"):
                arg = rng.choice("abc")
            elif op in ("JUMP", "JZ", "JNZ", "TRY"):
                arg = rng.randint(-2, max_len + 2)
            elif op == "ASSERT_DEPTH":
                arg = rng.randint(0, 5)
            else:
                arg = None
            prog.append((op, arg))
        inputs = [rng.randint(-5, 5) for _ in range(10)]
        cases.append(Case("fuzz_%03d" % i, prog, inputs=inputs,
                          max_steps=5000))
    return cases
