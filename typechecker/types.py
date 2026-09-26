"""类型表示与基础操作。

类型上的 origin 字段记录"该类型从何而来"（源码位置 + 描述），
用于在类型冲突时还原导致冲突的约束链。
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from .astnodes import Loc

Origin = Tuple[Loc, str]


class Type:
    origin: Optional[Origin] = None


class TCon(Type):
    __slots__ = ("name", "origin")

    def __init__(self, name: str, origin: Optional[Origin] = None):
        self.name = name  # 'Int' | 'Bool' | 'Str'
        self.origin = origin


class TList(Type):
    __slots__ = ("elem", "origin")

    def __init__(self, elem: Type, origin: Optional[Origin] = None):
        self.elem = elem
        self.origin = origin


class TFun(Type):
    __slots__ = ("arg", "ret", "origin")

    def __init__(self, arg: Type, ret: Type, origin: Optional[Origin] = None):
        self.arg = arg
        self.ret = ret
        self.origin = origin


class TVar(Type):
    """类型变量。instance 非空表示已被合一绑定；bound_by 记录绑定它的约束。"""
    __slots__ = ("id", "hint", "instance", "bound_by", "origin")
    _counter = 0

    def __init__(self, hint: Optional[str] = None, origin: Optional[Origin] = None):
        self.id = TVar._counter
        TVar._counter += 1
        self.hint = hint
        self.instance: Optional[Type] = None
        self.bound_by = None
        self.origin = origin


def prune(t: Type) -> Type:
    """迭代式路径压缩查找，避免深类型上的递归。"""
    while isinstance(t, TVar) and t.instance is not None:
        t = t.instance
    return t


def show(t: Type) -> str:
    t = prune(t)
    if isinstance(t, TCon):
        return t.name
    if isinstance(t, TVar):
        return t.hint or f"'t{t.id}"
    if isinstance(t, TList):
        return f"(List {show(t.elem)})"
    if isinstance(t, TFun):
        return f"({show(t.arg)} -> {show(t.ret)})"
    raise TypeError(f"未知类型: {t!r}")


def free_vars(t: Type) -> List[TVar]:
    """类型中未被绑定的类型变量（迭代实现）。"""
    out: List[TVar] = []
    seen: Set[int] = set()
    stack = [t]
    while stack:
        x = prune(stack.pop())
        if isinstance(x, TVar):
            if x.id not in seen:
                seen.add(x.id)
                out.append(x)
        elif isinstance(x, TFun):
            stack.append(x.arg)
            stack.append(x.ret)
        elif isinstance(x, TList):
            stack.append(x.elem)
    return out


def occurs_in_fun_arg(t: Type, var: TVar) -> bool:
    """var 是否出现在某函数类型的参数位置（用于区分真多态与完全无约束）。"""
    seen: Set[int] = set()
    stack = [(t, False)]
    while stack:
        x, in_arg = stack.pop()
        x = prune(x)
        if isinstance(x, TVar):
            if x is var and in_arg:
                return True
        elif isinstance(x, TFun):
            if id(x) in seen:
                continue
            seen.add(id(x))
            stack.append((x.arg, True))
            stack.append((x.ret, in_arg))
        elif isinstance(x, TList):
            if id(x) in seen:
                continue
            seen.add(id(x))
            stack.append((x.elem, in_arg))
    return False
