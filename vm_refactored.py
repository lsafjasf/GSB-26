"""重构后的字节码解释器：指令表驱动。

设计要点：
- 每条指令 = 一个独立的处理函数，显式接收执行上下文 Ctx，
  不依赖任何隐式共享的局部变量；
- DISPATCH 是唯一的分发出入口；@handles("NAME") 是唯一的注册点；
- 新增一条指令只需两处相邻改动：
    1) 实现：写一个 def op_xxx(ctx, arg) 函数；
    2) 注册：在函数上方加 @handles("XXX") 装饰器。
- 主循环 run() 只负责取指、计步、调用处理函数、收敛结果，
  不包含任何指令语义。
"""


class VMError(Exception):
    kind = "VMError"

    def __init__(self, msg=""):
        super().__init__(msg)
        self.msg = msg


class StackUnderflow(VMError):
    kind = "StackUnderflow"


class DivisionByZero(VMError):
    kind = "DivisionByZero"


class UndefinedVariable(VMError):
    kind = "UndefinedVariable"


class UnhandledException(VMError):
    kind = "UnhandledException"


class StackImbalance(VMError):
    kind = "StackImbalance"


class InvalidJump(VMError):
    kind = "InvalidJump"


class InputExhausted(VMError):
    kind = "InputExhausted"


class UnknownOpcode(VMError):
    kind = "UnknownOpcode"


class HandlerUnderflow(VMError):
    kind = "HandlerUnderflow"


class StepLimitExceeded(VMError):
    kind = "StepLimitExceeded"


class Result:
    """一次执行的最终结果：状态、返回值、输出、步数、错误信息。"""

    def __init__(self, status, value=None, output=None, steps=0,
                 error_kind=None, error_msg=""):
        self.status = status            # "ok" | "error"
        self.value = value              # RETURN 的返回值（HALT 为 None）
        self.output = output if output is not None else []
        self.steps = steps
        self.error_kind = error_kind
        self.error_msg = error_msg

    def __repr__(self):
        return ("Result(status=%r, value=%r, output=%r, steps=%r, "
                "error_kind=%r, error_msg=%r)"
                % (self.status, self.value, self.output, self.steps,
                   self.error_kind, self.error_msg))


class _Halt(Exception):
    """内部控制流信号：RETURN/HALT 通过它让主循环停机。"""

    def __init__(self, value):
        super().__init__()
        self.value = value


class Ctx:
    """执行上下文：指令处理函数唯一允许触碰的状态。

    包含栈、变量、输出、异常处理栈、输入队列、pc、步数。
    """

    __slots__ = ("program", "inputs", "max_steps", "stack", "vars",
                 "output", "handlers", "pc", "steps")

    def __init__(self, program, inputs, max_steps):
        self.program = program
        self.inputs = inputs
        self.max_steps = max_steps
        self.stack = []
        self.vars = {}
        self.output = []
        self.handlers = []      # (catch_addr, stack_depth) 的栈
        self.pc = 0
        self.steps = 0

    # ---- 显式栈操作：替代散落在各分支里的 stack.append/pop ----

    def push(self, value):
        self.stack.append(value)

    def pop1(self, who):
        if len(self.stack) < 1:
            raise StackUnderflow(
                "%s needs 1 value(s), stack has %d" % (who, len(self.stack)))
        return self.stack.pop()

    def pop2(self, who):
        if len(self.stack) < 2:
            raise StackUnderflow(
                "%s needs 2 value(s), stack has %d" % (who, len(self.stack)))
        b = self.stack.pop()
        a = self.stack.pop()
        return a, b

    # ---- 显式跳转：统一校验目标地址 ----

    def check_target(self, addr):
        if addr < 0 or addr >= len(self.program):
            raise InvalidJump(
                "jump target %d out of range [0,%d)" % (addr, len(self.program)))

    def jump(self, addr):
        self.check_target(addr)
        self.pc = addr


# ---------------------------------------------------------------------------
# 指令注册表与处理函数
# ---------------------------------------------------------------------------

DISPATCH = {}


def handles(opname):
    """注册指令处理函数：新增指令时唯一的注册点。"""
    def deco(fn):
        DISPATCH[opname] = fn
        return fn
    return deco


@handles("PUSH")
def op_push(ctx, arg):
    ctx.push(arg)


@handles("LOAD")
def op_load(ctx, arg):
    if arg not in ctx.vars:
        raise UndefinedVariable("undefined variable: %r" % (arg,))
    ctx.push(ctx.vars[arg])


@handles("STORE")
def op_store(ctx, arg):
    ctx.vars[arg] = ctx.pop1("STORE")


@handles("ADD")
def op_add(ctx, arg):
    a, b = ctx.pop2("ADD")
    ctx.push(a + b)


@handles("SUB")
def op_sub(ctx, arg):
    a, b = ctx.pop2("SUB")
    ctx.push(a - b)


@handles("MUL")
def op_mul(ctx, arg):
    a, b = ctx.pop2("MUL")
    ctx.push(a * b)


@handles("DIV")
def op_div(ctx, arg):
    a, b = ctx.pop2("DIV")
    if b == 0:
        raise DivisionByZero("integer division by zero")
    ctx.push(a // b)


@handles("MOD")
def op_mod(ctx, arg):
    a, b = ctx.pop2("MOD")
    if b == 0:
        raise DivisionByZero("integer modulo by zero")
    ctx.push(a % b)


@handles("NEG")
def op_neg(ctx, arg):
    ctx.push(-ctx.pop1("NEG"))


def _compare(ctx, who, pred):
    a, b = ctx.pop2(who)
    ctx.push(1 if pred(a, b) else 0)


@handles("EQ")
def op_eq(ctx, arg):
    _compare(ctx, "EQ", lambda a, b: a == b)


@handles("NE")
def op_ne(ctx, arg):
    _compare(ctx, "NE", lambda a, b: a != b)


@handles("LT")
def op_lt(ctx, arg):
    _compare(ctx, "LT", lambda a, b: a < b)


@handles("LE")
def op_le(ctx, arg):
    _compare(ctx, "LE", lambda a, b: a <= b)


@handles("GT")
def op_gt(ctx, arg):
    _compare(ctx, "GT", lambda a, b: a > b)


@handles("GE")
def op_ge(ctx, arg):
    _compare(ctx, "GE", lambda a, b: a >= b)


@handles("DUP")
def op_dup(ctx, arg):
    if len(ctx.stack) < 1:
        raise StackUnderflow(
            "DUP needs 1 value(s), stack has %d" % len(ctx.stack))
    ctx.push(ctx.stack[-1])


@handles("DROP")
def op_drop(ctx, arg):
    ctx.pop1("DROP")


@handles("SWAP")
def op_swap(ctx, arg):
    if len(ctx.stack) < 2:
        raise StackUnderflow(
            "SWAP needs 2 value(s), stack has %d" % len(ctx.stack))
    ctx.stack[-1], ctx.stack[-2] = ctx.stack[-2], ctx.stack[-1]


@handles("JUMP")
def op_jump(ctx, arg):
    ctx.jump(arg)


@handles("JZ")
def op_jz(ctx, arg):
    if ctx.pop1("JZ") == 0:
        ctx.jump(arg)


@handles("JNZ")
def op_jnz(ctx, arg):
    if ctx.pop1("JNZ") != 0:
        ctx.jump(arg)


@handles("TRY")
def op_try(ctx, arg):
    ctx.check_target(arg)
    ctx.handlers.append((arg, len(ctx.stack)))


@handles("ENDTRY")
def op_endtry(ctx, arg):
    if len(ctx.handlers) < 1:
        raise HandlerUnderflow("ENDTRY without matching TRY")
    ctx.handlers.pop()


@handles("THROW")
def op_throw(ctx, arg):
    exc = ctx.pop1("THROW")
    if len(ctx.handlers) == 0:
        raise UnhandledException("unhandled exception: %r" % (exc,))
    target, depth = ctx.handlers.pop()
    del ctx.stack[depth:]
    ctx.push(exc)
    ctx.pc = target


@handles("ASSERT_DEPTH")
def op_assert_depth(ctx, arg):
    if len(ctx.stack) != arg:
        raise StackImbalance(
            "stack depth %d != expected %d" % (len(ctx.stack), arg))


@handles("READ")
def op_read(ctx, arg):
    if len(ctx.inputs) == 0:
        raise InputExhausted("no more input")
    ctx.push(ctx.inputs.pop(0))


@handles("PRINT")
def op_print(ctx, arg):
    ctx.output.append(ctx.pop1("PRINT"))


@handles("RETURN")
def op_return(ctx, arg):
    raise _Halt(ctx.pop1("RETURN"))


@handles("HALT")
def op_halt(ctx, arg):
    raise _Halt(None)


# ---------------------------------------------------------------------------
# 主循环：取指 -> 计步 -> 查表分发 -> 收敛结果。不含任何指令语义。
# ---------------------------------------------------------------------------

DEFAULT_MAX_STEPS = 100000


def run(program, inputs=(), max_steps=DEFAULT_MAX_STEPS):
    ctx = Ctx(program, list(inputs), max_steps)
    n = len(program)
    while True:
        if ctx.pc < 0 or ctx.pc >= n:
            return Result("error", output=ctx.output, steps=ctx.steps,
                          error_kind="UnexpectedEnd",
                          error_msg="pc=%d out of program (len=%d)"
                                    % (ctx.pc, n))
        op, arg = program[ctx.pc]
        ctx.steps += 1
        if ctx.steps > max_steps:
            return Result("error", output=ctx.output, steps=ctx.steps,
                          error_kind="StepLimitExceeded",
                          error_msg="step limit %d exceeded" % max_steps)
        handler = DISPATCH.get(op)
        if handler is None:
            return Result("error", output=ctx.output, steps=ctx.steps,
                          error_kind="UnknownOpcode",
                          error_msg="unknown opcode: %r" % (op,))
        ctx.pc += 1
        try:
            handler(ctx, arg)
        except _Halt as halt:
            return Result("ok", value=halt.value, output=ctx.output,
                          steps=ctx.steps)
        except VMError as err:
            return Result("error", output=ctx.output, steps=ctx.steps,
                          error_kind=err.kind, error_msg=err.msg)
