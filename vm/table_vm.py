"""重构后：指令表驱动的字节码解释器。

每条指令 = 一个独立处理函数，显式接收执行上下文 Context
（栈、变量、pc、异常处理栈、输出、步数），不依赖任何隐式共享变量。
新增指令只需：一处实现（op_xxx 函数）+ 一处注册（@instruction 装饰器）。
"""

from collections import namedtuple

Result = namedtuple("Result", "ok value error steps output")

DEFAULT_STEP_LIMIT = 1_000_000


class VmError(Exception):
    """VM 运行时错误。kind 为错误类型名，payload 为被捕获时压栈的值。"""

    def __init__(self, kind, payload=None):
        super().__init__(kind)
        self.kind = kind
        self.payload = kind if payload is None else payload


class Context:
    """一次执行的全部状态，作为显式参数传给每个指令处理函数。"""

    __slots__ = (
        "program", "stack", "variables", "output", "inputs",
        "input_pos", "handlers", "pc", "steps", "halted",
    )

    def __init__(self, program, inputs):
        self.program = program
        self.stack = []
        self.variables = {}
        self.output = []
        self.inputs = list(inputs)
        self.input_pos = 0
        self.handlers = []  # (handler_addr, stack_depth)
        self.pc = 0
        self.steps = 0
        self.halted = False


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _require_stack(ctx, n):
    if len(ctx.stack) < n:
        raise VmError("StackUnderflow")


def _pop2(ctx):
    _require_stack(ctx, 2)
    b = ctx.stack.pop()
    a = ctx.stack.pop()
    return a, b


def _pop2_arith(ctx):
    a, b = _pop2(ctx)
    if not _is_int(a) or not _is_int(b):
        raise VmError("TypeError")
    return a, b


def _pop2_addable(ctx):
    a, b = _pop2(ctx)
    if isinstance(a, bool) or isinstance(b, bool):
        raise VmError("TypeError")
    if not isinstance(a, (int, str)) or type(a) is not type(b):
        raise VmError("TypeError")
    return a, b


def _check_target(ctx, arg):
    if not _is_int(arg) or not 0 <= arg < len(ctx.program):
        raise VmError("InvalidJump")
    return arg


# ---------------------------------------------------------------------------
# 指令表：op 名 -> 处理函数(ctx, arg)
# ---------------------------------------------------------------------------

DISPATCH = {}


def instruction(op):
    """注册一条指令：@instruction("xxx") 即完成注册。"""

    def deco(fn):
        DISPATCH[op] = fn
        return fn

    return deco


@instruction("push")
def op_push(ctx, arg):
    ctx.stack.append(arg)


@instruction("load")
def op_load(ctx, arg):
    if arg not in ctx.variables:
        raise VmError("UndefinedVariable")
    ctx.stack.append(ctx.variables[arg])


@instruction("store")
def op_store(ctx, arg):
    _require_stack(ctx, 1)
    ctx.variables[arg] = ctx.stack.pop()


@instruction("dup")
def op_dup(ctx, arg):
    _require_stack(ctx, 1)
    ctx.stack.append(ctx.stack[-1])


@instruction("drop")
def op_drop(ctx, arg):
    _require_stack(ctx, 1)
    ctx.stack.pop()


@instruction("swap")
def op_swap(ctx, arg):
    _require_stack(ctx, 2)
    ctx.stack[-1], ctx.stack[-2] = ctx.stack[-2], ctx.stack[-1]


@instruction("add")
def op_add(ctx, arg):
    a, b = _pop2_addable(ctx)
    ctx.stack.append(a + b)


@instruction("sub")
def op_sub(ctx, arg):
    a, b = _pop2_arith(ctx)
    ctx.stack.append(a - b)


@instruction("mul")
def op_mul(ctx, arg):
    a, b = _pop2_arith(ctx)
    ctx.stack.append(a * b)


@instruction("div")
def op_div(ctx, arg):
    a, b = _pop2_arith(ctx)
    if b == 0:
        raise VmError("ZeroDivisionError")
    ctx.stack.append(a // b)


@instruction("mod")
def op_mod(ctx, arg):
    a, b = _pop2_arith(ctx)
    if b == 0:
        raise VmError("ZeroDivisionError")
    ctx.stack.append(a % b)


@instruction("neg")
def op_neg(ctx, arg):
    _require_stack(ctx, 1)
    a = ctx.stack.pop()
    if not _is_int(a):
        raise VmError("TypeError")
    ctx.stack.append(-a)


@instruction("eq")
def op_eq(ctx, arg):
    a, b = _pop2(ctx)
    ctx.stack.append(a == b)


@instruction("ne")
def op_ne(ctx, arg):
    a, b = _pop2(ctx)
    ctx.stack.append(a != b)


@instruction("lt")
def op_lt(ctx, arg):
    a, b = _pop2_addable(ctx)
    ctx.stack.append(a < b)


@instruction("le")
def op_le(ctx, arg):
    a, b = _pop2_addable(ctx)
    ctx.stack.append(a <= b)


@instruction("gt")
def op_gt(ctx, arg):
    a, b = _pop2_addable(ctx)
    ctx.stack.append(a > b)


@instruction("ge")
def op_ge(ctx, arg):
    a, b = _pop2_addable(ctx)
    ctx.stack.append(a >= b)


@instruction("not")
def op_not(ctx, arg):
    _require_stack(ctx, 1)
    ctx.stack.append(not ctx.stack.pop())


@instruction("jmp")
def op_jmp(ctx, arg):
    ctx.pc = _check_target(ctx, arg)


@instruction("jz")
def op_jz(ctx, arg):
    _require_stack(ctx, 1)
    cond = ctx.stack.pop()
    if not cond:
        ctx.pc = _check_target(ctx, arg)


@instruction("try")
def op_try(ctx, arg):
    target = _check_target(ctx, arg)
    ctx.handlers.append((target, len(ctx.stack)))


@instruction("endtry")
def op_endtry(ctx, arg):
    if not ctx.handlers:
        raise VmError("HandlerUnderflow")
    ctx.handlers.pop()


@instruction("throw")
def op_throw(ctx, arg):
    _require_stack(ctx, 1)
    raise VmError("UncaughtException", ctx.stack.pop())


@instruction("print")
def op_print(ctx, arg):
    _require_stack(ctx, 1)
    ctx.output.append(ctx.stack.pop())


@instruction("input")
def op_input(ctx, arg):
    if ctx.input_pos >= len(ctx.inputs):
        raise VmError("InputExhausted")
    ctx.stack.append(ctx.inputs[ctx.input_pos])
    ctx.input_pos += 1


@instruction("halt")
def op_halt(ctx, arg):
    ctx.halted = True


# ---------------------------------------------------------------------------
# 主循环：取指 -> 计数 -> 查表分发 -> 统一异常处理
# ---------------------------------------------------------------------------

def run(program, inputs=(), step_limit=DEFAULT_STEP_LIMIT):
    ctx = Context(program, inputs)
    n = len(program)
    dispatch = DISPATCH.get  # 局部绑定，避免每次循环查全局

    while ctx.pc < n and not ctx.halted:
        if ctx.steps >= step_limit:
            return Result(False, None, "StepLimitExceeded", ctx.steps, tuple(ctx.output))
        instr = program[ctx.pc]
        op = instr[0]
        arg = instr[1] if len(instr) > 1 else None
        ctx.pc += 1
        ctx.steps += 1
        handler = dispatch(op)
        try:
            if handler is None:
                raise VmError("UnknownOp")
            handler(ctx, arg)
        except VmError as e:
            if ctx.handlers:
                addr, depth = ctx.handlers.pop()
                del ctx.stack[depth:]
                ctx.stack.append(e.payload)
                ctx.pc = addr
            else:
                return Result(False, None, e.kind, ctx.steps, tuple(ctx.output))

    if len(ctx.stack) != 1:
        return Result(False, None, "StackImbalance", ctx.steps, tuple(ctx.output))
    return Result(True, ctx.stack[0], None, ctx.steps, tuple(ctx.output))
