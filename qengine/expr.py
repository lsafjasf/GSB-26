"""行表达式：可 pickle 的纯数据表示（嵌套 tuple），跨进程直接传递。

表示法（前缀式嵌套 tuple）：
  ("lit", v)                 字面量
  ("col", name)              列引用
  ("add"|"sub"|"mul"|"div"|"mod", a, b)
  ("eq"|"ne"|"lt"|"le"|"gt"|"ge", a, b)
  ("and"|"or", a, b)
  ("not", a)
  ("in", a, (v1, v2, ...))
  ("isnull", a) / ("notnull", a)

NULL 语义（与 SQL 一致的三值逻辑简化版）：
  - 算术/比较：任一操作数为 None -> None
  - isnull/notnull 不传播 None
  - and/or/not 对 None 采用 SQL 三值逻辑
  - 过滤条件为 None 视为 False（该行被过滤掉）
"""

from __future__ import annotations

# ---------- 构造函数（便于手写查询计划） ----------

def lit(v):
    return ("lit", v)


def col(name):
    return ("col", name)


def binop(op, a, b):
    return (op, a, b)


def eq(a, b): return binop("eq", a, b)
def ne(a, b): return binop("ne", a, b)
def lt(a, b): return binop("lt", a, b)
def le(a, b): return binop("le", a, b)
def gt(a, b): return binop("gt", a, b)
def ge(a, b): return binop("ge", a, b)
def add(a, b): return binop("add", a, b)
def sub(a, b): return binop("sub", a, b)
def mul(a, b): return binop("mul", a, b)
def div(a, b): return binop("div", a, b)
def mod(a, b): return binop("mod", a, b)


def and_(*args):
    out = args[0]
    for a in args[1:]:
        out = ("and", out, a)
    return out


def or_(*args):
    out = args[0]
    for a in args[1:]:
        out = ("or", out, a)
    return out


def not_(a):
    return ("not", a)


def in_(a, values):
    return ("in", a, tuple(values))


def isnull(a):
    return ("isnull", a)


def notnull(a):
    return ("notnull", a)


# ---------- 求值 ----------

def _truth(v):
    """SQL 三值逻辑的真值：None -> None，其余按 Python bool。"""
    if v is None:
        return None
    return bool(v)


def eval_expr(expr, row):
    op = expr[0]
    if op == "lit":
        return expr[1]
    if op == "col":
        return row[expr[1]]
    if op == "isnull":
        return eval_expr(expr[1], row) is None
    if op == "notnull":
        return eval_expr(expr[1], row) is not None
    if op == "not":
        t = _truth(eval_expr(expr[1], row))
        return None if t is None else (not t)
    if op == "and":
        a = _truth(eval_expr(expr[1], row))
        if a is False:
            return False
        b = _truth(eval_expr(expr[2], row))
        # True AND b -> b; None AND False -> False; None AND True/None -> None
        if a is True:
            return b
        return False if b is False else None
    if op == "or":
        a = _truth(eval_expr(expr[1], row))
        if a is True:
            return True
        b = _truth(eval_expr(expr[2], row))
        # False OR b -> b; None OR True -> True; None OR False/None -> None
        if a is False:
            return b
        return True if b is True else None
    if op == "in":
        v = eval_expr(expr[1], row)
        if v is None:
            return None
        return v in expr[2]
    # 二元算术 / 比较：None 传播
    a = eval_expr(expr[1], row)
    b = eval_expr(expr[2], row)
    if a is None or b is None:
        return None
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if op == "div":
        return a / b
    if op == "mod":
        return a % b
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    if op == "gt":
        return a > b
    if op == "ge":
        return a >= b
    raise ValueError(f"unknown expr op: {op!r}")


def filter_match(expr, row):
    """过滤谓词：None 视为 False。"""
    v = eval_expr(expr, row)
    return v is not None and bool(v)
