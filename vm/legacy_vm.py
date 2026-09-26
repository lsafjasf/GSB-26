"""重构前：条件分发式字节码解释器（遗留实现）。

所有指令挤在 run() 的一个 if/elif 链里，共享 stack/variables/pc 等
一堆局部变量；新增指令必须在这条几百行的链里加分支。
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


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def run(program, inputs=(), step_limit=DEFAULT_STEP_LIMIT):
    stack = []
    variables = {}
    output = []
    inputs = list(inputs)
    input_pos = 0
    handlers = []  # (handler_addr, stack_depth)
    pc = 0
    steps = 0
    halted = False
    n = len(program)

    while pc < n and not halted:
        if steps >= step_limit:
            return Result(False, None, "StepLimitExceeded", steps, tuple(output))
        instr = program[pc]
        op = instr[0]
        arg = instr[1] if len(instr) > 1 else None
        pc += 1
        steps += 1
        try:
            if op == "push":
                stack.append(arg)
            elif op == "load":
                if arg not in variables:
                    raise VmError("UndefinedVariable")
                stack.append(variables[arg])
            elif op == "store":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                variables[arg] = stack.pop()
            elif op == "dup":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                stack.append(stack[-1])
            elif op == "drop":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                stack.pop()
            elif op == "swap":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                stack[-1], stack[-2] = stack[-2], stack[-1]
            elif op == "add":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, bool) or isinstance(b, bool):
                    raise VmError("TypeError")
                if not isinstance(a, (int, str)) or type(a) is not type(b):
                    raise VmError("TypeError")
                stack.append(a + b)
            elif op == "sub":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if not _is_int(a) or not _is_int(b):
                    raise VmError("TypeError")
                stack.append(a - b)
            elif op == "mul":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if not _is_int(a) or not _is_int(b):
                    raise VmError("TypeError")
                stack.append(a * b)
            elif op == "div":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if not _is_int(a) or not _is_int(b):
                    raise VmError("TypeError")
                if b == 0:
                    raise VmError("ZeroDivisionError")
                stack.append(a // b)
            elif op == "mod":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if not _is_int(a) or not _is_int(b):
                    raise VmError("TypeError")
                if b == 0:
                    raise VmError("ZeroDivisionError")
                stack.append(a % b)
            elif op == "neg":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                a = stack.pop()
                if not _is_int(a):
                    raise VmError("TypeError")
                stack.append(-a)
            elif op == "eq":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                stack.append(a == b)
            elif op == "ne":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                stack.append(a != b)
            elif op == "lt":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, bool) or isinstance(b, bool):
                    raise VmError("TypeError")
                if not isinstance(a, (int, str)) or type(a) is not type(b):
                    raise VmError("TypeError")
                stack.append(a < b)
            elif op == "le":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, bool) or isinstance(b, bool):
                    raise VmError("TypeError")
                if not isinstance(a, (int, str)) or type(a) is not type(b):
                    raise VmError("TypeError")
                stack.append(a <= b)
            elif op == "gt":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, bool) or isinstance(b, bool):
                    raise VmError("TypeError")
                if not isinstance(a, (int, str)) or type(a) is not type(b):
                    raise VmError("TypeError")
                stack.append(a > b)
            elif op == "ge":
                if len(stack) < 2:
                    raise VmError("StackUnderflow")
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, bool) or isinstance(b, bool):
                    raise VmError("TypeError")
                if not isinstance(a, (int, str)) or type(a) is not type(b):
                    raise VmError("TypeError")
                stack.append(a >= b)
            elif op == "not":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                stack.append(not stack.pop())
            elif op == "jmp":
                if not _is_int(arg) or not 0 <= arg < n:
                    raise VmError("InvalidJump")
                pc = arg
            elif op == "jz":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                cond = stack.pop()
                if not cond:
                    if not _is_int(arg) or not 0 <= arg < n:
                        raise VmError("InvalidJump")
                    pc = arg
            elif op == "try":
                if not _is_int(arg) or not 0 <= arg < n:
                    raise VmError("InvalidJump")
                handlers.append((arg, len(stack)))
            elif op == "endtry":
                if not handlers:
                    raise VmError("HandlerUnderflow")
                handlers.pop()
            elif op == "throw":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                raise VmError("UncaughtException", stack.pop())
            elif op == "print":
                if len(stack) < 1:
                    raise VmError("StackUnderflow")
                output.append(stack.pop())
            elif op == "input":
                if input_pos >= len(inputs):
                    raise VmError("InputExhausted")
                stack.append(inputs[input_pos])
                input_pos += 1
            elif op == "halt":
                halted = True
            else:
                raise VmError("UnknownOp")
        except VmError as e:
            if handlers:
                addr, depth = handlers.pop()
                del stack[depth:]
                stack.append(e.payload)
                pc = addr
            else:
                return Result(False, None, e.kind, steps, tuple(output))

    if len(stack) != 1:
        return Result(False, None, "StackImbalance", steps, tuple(output))
    return Result(True, stack[0], None, steps, tuple(output))
