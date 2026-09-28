"""IR 解释器：定义语言的运行时语义。

- 64 位有符号环绕语义：每次 ``const``/``binop`` 结果都回绕到 int64。
- ``div``/``mod`` 为 C 风格截断除法；除零抛出 :class:`Trap`。
- 读取未定义变量得到 0；输入流耗尽后 ``input`` 读到 0。
- 步数上限用于把死循环识别为 ``step_limit`` 状态（如自跳转）。
"""
from __future__ import annotations

from .ir import is_int

WORD_BITS = 64
_MASK = (1 << WORD_BITS) - 1
_SIGN = 1 << (WORD_BITS - 1)


def wrap(value: int) -> int:
    """回绕到 64 位有符号整数。"""
    value &= _MASK
    return value - (1 << WORD_BITS) if value >= _SIGN else value


class Trap(Exception):
    """运行时陷阱（目前唯一来源：除零）。"""


def eval_binop(op: str, a: int, b: int) -> int:
    """按运行时语义计算二元运算；除零抛 Trap。编译期折叠复用此函数以保证语义一致。"""
    if op == "add":
        return wrap(a + b)
    if op == "sub":
        return wrap(a - b)
    if op == "mul":
        return wrap(a * b)
    if op == "div":
        if b == 0:
            raise Trap("division by zero")
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        return wrap(q)
    if op == "mod":
        if b == 0:
            raise Trap("division by zero")
        r = abs(a) % abs(b)
        if a < 0:
            r = -r
        return wrap(r)
    if op == "lt":
        return 1 if a < b else 0
    if op == "le":
        return 1 if a <= b else 0
    if op == "gt":
        return 1 if a > b else 0
    if op == "ge":
        return 1 if a >= b else 0
    if op == "eq":
        return 1 if a == b else 0
    if op == "ne":
        return 1 if a != b else 0
    raise ValueError(f"unknown binop {op!r}")


def run(prog, inputs=(), step_limit=100_000) -> dict:
    """执行程序，返回 {"status", "outputs", "steps", "error"?}。

    status ∈ {"halt", "trap", "step_limit"}；不抛异常，便于对拍比较。
    """
    labels = prog.label_map()
    env = {}
    outputs = []
    inputs = list(inputs)
    pc = 0
    steps = 0
    n = len(prog.instrs)

    def val(x):
        return int(x) if is_int(x) else env.get(x, 0)

    try:
        while 0 <= pc < n:
            steps += 1
            if steps > step_limit:
                return {"status": "step_limit", "outputs": outputs, "steps": steps}
            ins = prog.instrs[pc]
            op, args = ins.op, ins.args
            if op == "const":
                env[args[0]] = wrap(int(args[1]))
                pc += 1
            elif op == "input":
                env[args[0]] = wrap(inputs.pop(0)) if inputs else 0
                pc += 1
            elif op == "mov":
                env[args[0]] = val(args[1])
                pc += 1
            elif op == "binop":
                env[args[0]] = eval_binop(args[1], val(args[2]), val(args[3]))
                pc += 1
            elif op == "print":
                outputs.append(val(args[0]))
                pc += 1
            elif op == "jmp":
                pc = labels[args[0]]
            elif op == "cjmp":
                pc = labels[args[1]] if val(args[0]) != 0 else labels[args[2]]
            elif op == "halt":
                return {"status": "halt", "outputs": outputs, "steps": steps}
            else:  # pragma: no cover
                raise ValueError(f"unknown op {op!r}")
        return {"status": "halt", "outputs": outputs, "steps": steps}
    except Trap as exc:
        return {"status": "trap", "outputs": outputs, "steps": steps, "error": str(exc)}


def signature(result: dict):
    """用于对拍比较的规范化结果（忽略步数，因优化会改变步数）。"""
    return (result["status"], tuple(result["outputs"]), result.get("error"))


def equivalent(before: dict, after: dict) -> bool:
    """对拍等价判据。

    - 一般情形：状态、全部输出、陷阱信息必须完全相等（见 :func:`signature`）。
    - 双方均为 ``step_limit``（都超时）：按**输出前缀**比较——较短的一方
      必须是较长一方的前缀。优化会改变每次循环迭代执行的指令条数，
      因此在相同步数上限内两侧输出的条数可以不同，但已产生的输出
      序列必须一致（短者是长者的前缀）。
    """
    if before["status"] == after["status"] == "step_limit":
        a, b = before["outputs"], after["outputs"]
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return longer[: len(shorter)] == shorter
    return signature(before) == signature(after)
