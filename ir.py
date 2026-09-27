"""中间表示 (IR) 定义与解释器。

指令集（所有指令为 Instr(op, args) 元组式结构）：
    ('label', name)            跳转目标标记（伪指令，不计入语句数）
    ('const', dst, value)      dst = value（64 位有符号回绕）
    ('input', dst, idx)        dst = inputs[idx]（越界时为 0）
    ('mov',   dst, src)        dst = src
    ('binop', dst, op, a, b)   dst = a op b，op ∈ + - * // % < <= == != > >=
    ('print', reg)             输出 reg 的值
    ('jmp',   label)           无条件跳转
    ('jz',    cond, label)     cond == 0 时跳转
    ('jnz',   cond, label)     cond != 0 时跳转
    ('halt',)                  停机
    ('ret',   reg)             返回 reg 的值

机器语义：所有算术按 64 位有符号回绕（wrap-around）进行，
因此编译期折叠与运行时求值逐位一致（见 README「保守策略」）。
整除为向零截断；除数为零在运行时产生可观察的 divzero 结果。
"""
from dataclasses import dataclass

INT64_MIN = -(1 << 63)
MASK64 = (1 << 64) - 1


def wrap64(value):
    """把任意整数回绕到 64 位有符号区间。"""
    return ((value - INT64_MIN) & MASK64) + INT64_MIN


class DivZero(Exception):
    """运行时除零（可观察行为，优化不得改变它）。"""


class StepLimit(Exception):
    """解释步数超限（用于识别死循环，如跳转到自身）。"""


@dataclass(frozen=True)
class Instr:
    op: str
    args: tuple = ()

    def __repr__(self):
        if self.args:
            return f"{self.op} {' '.join(map(str, self.args))}"
        return self.op


ARITH_OPS = frozenset({'+', '-', '*', '//', '%'})
CMP_OPS = frozenset({'<', '<=', '==', '!=', '>', '>='})
BINOPS = ARITH_OPS | CMP_OPS
DIV_OPS = frozenset({'//', '%'})


def eval_binop(op, a, b):
    """按机器语义求值；调用方保证 op 为 // 或 % 时 b != 0。"""
    if op == '+':
        return wrap64(a + b)
    if op == '-':
        return wrap64(a - b)
    if op == '*':
        return wrap64(a * b)
    if op == '//':
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        return wrap64(q)
    if op == '%':
        return wrap64(a - eval_binop('//', a, b) * b)
    if op == '<':
        return 1 if a < b else 0
    if op == '<=':
        return 1 if a <= b else 0
    if op == '==':
        return 1 if a == b else 0
    if op == '!=':
        return 1 if a != b else 0
    if op == '>':
        return 1 if a > b else 0
    if op == '>=':
        return 1 if a >= b else 0
    raise ValueError(f"unknown binop {op!r}")


def foldable(op, a, b):
    """编译期是否允许折叠该常量运算（除零保守策略的核心）。"""
    if op in DIV_OPS and b == 0:
        return False
    return True


def run(prog, inputs=(), step_limit=200_000):
    """解释执行程序，返回可比较的结果元组。

    结果形式：
        ('ret', value, outputs)   正常返回
        ('halt', outputs)         停机
        ('falloff', outputs)      执行流走出末尾
        ('divzero', outputs)      运行时除零
        ('timeout', outputs)      超过步数上限（死循环）
    """
    labels = {ins.args[0]: i for i, ins in enumerate(prog) if ins.op == 'label'}
    env = {}
    outputs = []
    pc = 0
    steps = 0

    def get(reg):
        if reg not in env:
            raise RuntimeError(f"undefined register {reg!r}")
        return env[reg]

    try:
        while pc < len(prog):
            steps += 1
            if steps > step_limit:
                raise StepLimit()
            ins = prog[pc]
            op = ins.op
            if op == 'label':
                pc += 1
            elif op == 'const':
                env[ins.args[0]] = wrap64(ins.args[1])
                pc += 1
            elif op == 'input':
                dst, idx = ins.args
                env[dst] = wrap64(inputs[idx]) if idx < len(inputs) else 0
                pc += 1
            elif op == 'mov':
                env[ins.args[0]] = get(ins.args[1])
                pc += 1
            elif op == 'binop':
                dst, bop, a, b = ins.args
                va, vb = get(a), get(b)
                if bop in DIV_OPS and vb == 0:
                    raise DivZero()
                env[dst] = eval_binop(bop, va, vb)
                pc += 1
            elif op == 'print':
                outputs.append(get(ins.args[0]))
                pc += 1
            elif op == 'jmp':
                pc = labels[ins.args[0]]
            elif op == 'jz':
                cond, label = ins.args
                pc = labels[label] if get(cond) == 0 else pc + 1
            elif op == 'jnz':
                cond, label = ins.args
                pc = labels[label] if get(cond) != 0 else pc + 1
            elif op == 'halt':
                return ('halt', tuple(outputs))
            elif op == 'ret':
                return ('ret', get(ins.args[0]), tuple(outputs))
            else:
                raise RuntimeError(f"bad opcode {op!r}")
        return ('falloff', tuple(outputs))
    except DivZero:
        return ('divzero', tuple(outputs))
    except StepLimit:
        return ('timeout', tuple(outputs))


def stmt_count(prog):
    """语句数（label 伪指令不计）。"""
    return sum(1 for ins in prog if ins.op != 'label')
