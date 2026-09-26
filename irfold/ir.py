"""中间表示（IR）定义、文本解析与打印。

指令集（每条指令可带一个可选标签 ``label:``）::

    const  dst, VALUE          # 常量赋值
    input  dst                 # 从输入流读取一个整数
    mov    dst, src            # 复制
    binop  dst, OP, a, b       # 二元运算, OP in add/sub/mul/div/mod/lt/le/gt/ge/eq/ne
    print  src                 # 可观察输出
    jmp    LABEL               # 无条件跳转
    cjmp   cond, L1, L2        # cond != 0 跳 L1, 否则跳 L2
    halt                       # 停机

操作数可以是变量名或整数字面量。读取未定义变量得到 0。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BINOPS = {"add", "sub", "mul", "div", "mod", "lt", "le", "gt", "ge", "eq", "ne"}
OPS = {"const", "input", "mov", "binop", "print", "jmp", "cjmp", "halt"}

_LABEL_RE = re.compile(r"^([A-Za-z_]\w*)\s*:\s*(.*)$")


def is_int(x) -> bool:
    if isinstance(x, int):
        return True
    try:
        int(x)
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class Instr:
    op: str
    args: list
    label: str | None = None

    def text(self) -> str:
        body = " ".join([self.op, *[str(a) for a in self.args]])
        return f"{self.label}: {body}" if self.label else body


class Program:
    def __init__(self, instrs):
        self.instrs = list(instrs)

    def __len__(self):
        return len(self.instrs)

    def label_map(self) -> dict:
        m = {}
        for i, ins in enumerate(self.instrs):
            if ins.label is not None:
                if ins.label in m:
                    raise ValueError(f"duplicate label: {ins.label}")
                m[ins.label] = i
        return m

    def dump(self) -> str:
        return "\n".join(ins.text() for ins in self.instrs)


def parse(text: str) -> Program:
    instrs = []
    pending = []          # 等待附着到指令上的标签（允许连续多个）
    aliases = {}          # 多余标签 -> 主标签
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _LABEL_RE.match(line)
        if m:
            pending.append(m.group(1))
            line = m.group(2).strip()
            if not line:
                continue
        tokens = re.split(r"[,\s]+", line)
        op, args = tokens[0], tokens[1:]
        if op not in OPS:
            raise ValueError(f"line {lineno}: unknown op {op!r}")
        if op == "binop" and args[1] not in BINOPS:
            raise ValueError(f"line {lineno}: unknown binop {args[1]!r}")
        label = pending[0] if pending else None
        for extra in pending[1:]:
            aliases[extra] = label
        pending = []
        instrs.append(Instr(op, args, label))
    if pending:
        raise ValueError(f"label {pending[-1]!r} not attached to any instruction")
    prog = Program(instrs)
    if aliases:  # 把跳转到别名的目标重写为主标签
        for ins in prog.instrs:
            if ins.op == "jmp":
                ins.args[0] = aliases.get(ins.args[0], ins.args[0])
            elif ins.op == "cjmp":
                ins.args[1] = aliases.get(ins.args[1], ins.args[1])
                ins.args[2] = aliases.get(ins.args[2], ins.args[2])
    _check_labels(prog)
    return prog


def _check_labels(prog: Program) -> None:
    labels = prog.label_map()
    for ins in prog.instrs:
        refs = []
        if ins.op == "jmp":
            refs = [ins.args[0]]
        elif ins.op == "cjmp":
            refs = [ins.args[1], ins.args[2]]
        for r in refs:
            if r not in labels:
                raise ValueError(f"undefined label: {r}")


def successors(prog: Program) -> list:
    """每条指令的后继下标列表（构建控制流图）。"""
    n = len(prog.instrs)
    labels = prog.label_map()
    succs = []
    for i, ins in enumerate(prog.instrs):
        if ins.op == "jmp":
            succs.append([labels[ins.args[0]]])
        elif ins.op == "cjmp":
            succs.append([labels[ins.args[1]], labels[ins.args[2]]])
        elif ins.op == "halt":
            succs.append([])
        else:
            succs.append([i + 1] if i + 1 < n else [])
    return succs


def uses(ins: Instr) -> list:
    """指令读取的变量。"""
    if ins.op == "mov":
        return [ins.args[1]] if not is_int(ins.args[1]) else []
    if ins.op == "binop":
        return [a for a in ins.args[2:] if not is_int(a)]
    if ins.op == "print":
        return [ins.args[0]] if not is_int(ins.args[0]) else []
    if ins.op == "cjmp":
        return [ins.args[0]] if not is_int(ins.args[0]) else []
    return []


def defs(ins: Instr) -> list:
    """指令写入的变量。"""
    if ins.op in ("const", "input", "mov", "binop"):
        return [ins.args[0]]
    return []
