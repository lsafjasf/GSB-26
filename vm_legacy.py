"""重构前的字节码解释器（遗留实现，作为对拍基线）。

遗留问题：
- 全部指令挤在 run() 的一个 if/elif 链里，几百行；
- 指令之间隐式共享 run() 的局部变量：stack/variables/pc/steps/output/handlers；
- 栈操作（push/pop、下溢检查）在每个分支里重复书写；
- 新增一条指令要在长长的 dispatch 链里找位置插入，并手工处理所有共享状态。
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


def run(program, inputs=(), max_steps=100000):
    """执行字节码程序。

    program: [(op, arg), ...]
    inputs:  READ 指令依次消费的输入序列
    """
    stack = []
    variables = {}
    output = []
    handlers = []          # (catch_addr, stack_depth) 的栈
    inputs = list(inputs)
    n = len(program)
    pc = 0
    steps = 0

    while True:
        if pc < 0 or pc >= n:
            return Result("error", output=output, steps=steps,
                          error_kind="UnexpectedEnd",
                          error_msg="pc=%d out of program (len=%d)" % (pc, n))
        op = program[pc][0]
        arg = program[pc][1]
        steps += 1
        if steps > max_steps:
            return Result("error", output=output, steps=steps,
                          error_kind="StepLimitExceeded",
                          error_msg="step limit %d exceeded" % max_steps)
        try:
            if op == "PUSH":
                stack.append(arg)
                pc += 1
            elif op == "LOAD":
                if arg not in variables:
                    raise UndefinedVariable("undefined variable: %r" % (arg,))
                stack.append(variables[arg])
                pc += 1
            elif op == "STORE":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "STORE needs 1 value(s), stack has %d" % len(stack))
                variables[arg] = stack.pop()
                pc += 1
            elif op == "ADD":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "ADD needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(a + b)
                pc += 1
            elif op == "SUB":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "SUB needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(a - b)
                pc += 1
            elif op == "MUL":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "MUL needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(a * b)
                pc += 1
            elif op == "DIV":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "DIV needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                if b == 0:
                    raise DivisionByZero("integer division by zero")
                stack.append(a // b)
                pc += 1
            elif op == "MOD":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "MOD needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                if b == 0:
                    raise DivisionByZero("integer modulo by zero")
                stack.append(a % b)
                pc += 1
            elif op == "NEG":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "NEG needs 1 value(s), stack has %d" % len(stack))
                stack.append(-stack.pop())
                pc += 1
            elif op == "EQ":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "EQ needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a == b else 0)
                pc += 1
            elif op == "NE":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "NE needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a != b else 0)
                pc += 1
            elif op == "LT":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "LT needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a < b else 0)
                pc += 1
            elif op == "LE":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "LE needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a <= b else 0)
                pc += 1
            elif op == "GT":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "GT needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a > b else 0)
                pc += 1
            elif op == "GE":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "GE needs 2 value(s), stack has %d" % len(stack))
                b = stack.pop()
                a = stack.pop()
                stack.append(1 if a >= b else 0)
                pc += 1
            elif op == "DUP":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "DUP needs 1 value(s), stack has %d" % len(stack))
                stack.append(stack[-1])
                pc += 1
            elif op == "DROP":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "DROP needs 1 value(s), stack has %d" % len(stack))
                stack.pop()
                pc += 1
            elif op == "SWAP":
                if len(stack) < 2:
                    raise StackUnderflow(
                        "SWAP needs 2 value(s), stack has %d" % len(stack))
                stack[-1], stack[-2] = stack[-2], stack[-1]
                pc += 1
            elif op == "JUMP":
                if arg < 0 or arg >= n:
                    raise InvalidJump(
                        "jump target %d out of range [0,%d)" % (arg, n))
                pc = arg
            elif op == "JZ":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "JZ needs 1 value(s), stack has %d" % len(stack))
                cond = stack.pop()
                if cond == 0:
                    if arg < 0 or arg >= n:
                        raise InvalidJump(
                            "jump target %d out of range [0,%d)" % (arg, n))
                    pc = arg
                else:
                    pc += 1
            elif op == "JNZ":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "JNZ needs 1 value(s), stack has %d" % len(stack))
                cond = stack.pop()
                if cond != 0:
                    if arg < 0 or arg >= n:
                        raise InvalidJump(
                            "jump target %d out of range [0,%d)" % (arg, n))
                    pc = arg
                else:
                    pc += 1
            elif op == "TRY":
                if arg < 0 or arg >= n:
                    raise InvalidJump(
                        "jump target %d out of range [0,%d)" % (arg, n))
                handlers.append((arg, len(stack)))
                pc += 1
            elif op == "ENDTRY":
                if len(handlers) < 1:
                    raise HandlerUnderflow("ENDTRY without matching TRY")
                handlers.pop()
                pc += 1
            elif op == "THROW":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "THROW needs 1 value(s), stack has %d" % len(stack))
                exc = stack.pop()
                if len(handlers) == 0:
                    raise UnhandledException(
                        "unhandled exception: %r" % (exc,))
                target = handlers[-1][0]
                depth = handlers[-1][1]
                handlers.pop()
                del stack[depth:]
                stack.append(exc)
                pc = target
            elif op == "ASSERT_DEPTH":
                if len(stack) != arg:
                    raise StackImbalance(
                        "stack depth %d != expected %d" % (len(stack), arg))
                pc += 1
            elif op == "READ":
                if len(inputs) == 0:
                    raise InputExhausted("no more input")
                stack.append(inputs.pop(0))
                pc += 1
            elif op == "PRINT":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "PRINT needs 1 value(s), stack has %d" % len(stack))
                output.append(stack.pop())
                pc += 1
            elif op == "RETURN":
                if len(stack) < 1:
                    raise StackUnderflow(
                        "RETURN needs 1 value(s), stack has %d" % len(stack))
                return Result("ok", value=stack.pop(), output=output,
                              steps=steps)
            elif op == "HALT":
                return Result("ok", value=None, output=output, steps=steps)
            else:
                raise UnknownOpcode("unknown opcode: %r" % (op,))
        except VMError as e:
            return Result("error", output=output, steps=steps,
                          error_kind=e.kind, error_msg=e.msg)
