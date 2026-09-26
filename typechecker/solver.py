"""约束求解器：收集等式约束，用带 occurs check 的合一（union-find）求解。

终止性保证：
- 每条约束只被处理一次，约束数量在生成阶段已有限；
- occurs check 拒绝 a = a -> b 这类无限类型，直接报错而不是展开；
- 合一沿有限的类型结构递归，类型图无环（由 occurs check 保证）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .astnodes import Loc
from .types import TCon, TFun, TList, TVar, Type, prune


@dataclass
class Constraint:
    a: Type          # 期望一侧
    b: Type          # 实际一侧
    reason: str      # 该约束为何产生（人类可读）
    loc: Loc         # 产生该约束的表达式位置


class Conflict(Exception):
    """两个具体类型构造子不一致。"""

    def __init__(self, expected: Type, actual: Type, constraint: Constraint, path: str):
        super().__init__("type conflict")
        self.expected = expected
        self.actual = actual
        self.constraint = constraint
        self.path = path  # 如 "函数返回值 -> 函数参数"


class InfiniteType(Exception):
    """occurs check 失败：递归（无限）类型。"""

    def __init__(self, var: TVar, t: Type, constraint: Constraint):
        super().__init__("infinite type")
        self.var = var
        self.type = t
        self.constraint = constraint


def _occurs(var: TVar, t: Type) -> bool:
    """迭代式 occurs check，避免深类型上的递归。"""
    seen = set()
    stack = [t]
    while stack:
        x = prune(stack.pop())
        if x is var:
            return True
        if id(x) in seen:
            continue
        seen.add(id(x))
        if isinstance(x, TFun):
            stack.append(x.arg)
            stack.append(x.ret)
        elif isinstance(x, TList):
            stack.append(x.elem)
    return False


def _bind(var: TVar, t: Type, constraint: Constraint) -> None:
    if _occurs(var, t):
        raise InfiniteType(var, t, constraint)
    var.instance = t
    var.bound_by = constraint


def _join(path: str, part: str) -> str:
    return f"{path} -> {part}" if path else part


def unify(a: Type, b: Type, constraint: Constraint, path: str = "") -> None:
    a = prune(a)
    b = prune(b)
    if a is b:
        return
    if isinstance(a, TVar):
        _bind(a, b, constraint)
        return
    if isinstance(b, TVar):
        _bind(b, a, constraint)
        return
    if isinstance(a, TCon) and isinstance(b, TCon) and a.name == b.name:
        return
    if type(a) is not type(b) or isinstance(a, TCon):
        raise Conflict(a, b, constraint, path)
    if isinstance(a, TFun) and isinstance(b, TFun):
        unify(a.arg, b.arg, constraint, _join(path, "函数参数"))
        unify(a.ret, b.ret, constraint, _join(path, "函数返回值"))
        return
    if isinstance(a, TList) and isinstance(b, TList):
        unify(a.elem, b.elem, constraint, _join(path, "列表元素"))
        return
    raise Conflict(a, b, constraint, path)


class Solver:
    """约束队列。checkpoint/solve_from 支持在 let/递归组边界增量求解。"""

    def __init__(self) -> None:
        self.constraints: List[Constraint] = []
        self.solved = 0  # 已处理的约束下标，保证每条约束只被求解一次

    def checkpoint(self) -> int:
        return len(self.constraints)

    def add(self, a: Type, b: Type, reason: str, loc: Loc) -> None:
        self.constraints.append(Constraint(a, b, reason, loc))

    def solve_from(self, index: int) -> List[Exception]:
        """求解 [index, ...) 的约束；冲突被收集而非抛出，以便报告多个错误。"""
        errors: List[Exception] = []
        index = max(index, self.solved)
        while index < len(self.constraints):
            constraint = self.constraints[index]
            index += 1
            try:
                unify(constraint.a, constraint.b, constraint)
            except (Conflict, InfiniteType) as exc:
                errors.append(exc)
        self.solved = max(self.solved, index)
        return errors
