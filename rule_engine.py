"""规则表引擎。

每条规则声明：
    rule_id    规则编号（冲突/不可达报告中使用）
    priority   显式优先级（整数，大者先匹配）——匹配顺序只由 priority 决定，
               与声明顺序无关
    conditions 条件元组 ((字段, 运算符, 值), ...)，多个条件为 AND 关系；
               运算符支持 == != > >= < <= in
    result     判定结果（任意对象；可含 callable，由调用方解析）
    default    是否为默认分支；每张表必须恰好一条默认规则

加载（Engine 构造）时校验：
    1. 默认规则必须恰好一条（默认分支显式声明）；
    2. 条件自相矛盾的规则 -> UNREACHABLE；
    3. 被更高/相等优先级规则完全遮蔽的规则 -> UNREACHABLE；
    4. 条件重叠、优先级相同但结果不同的规则对 -> CONFLICT。
校验失败抛出 RuleTableError，并指出规则编号。
"""

import operator
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

Condition = Tuple[str, str, Any]

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "in": lambda v, s: v in s,
}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    priority: int
    conditions: Tuple[Condition, ...]
    result: Any
    default: bool = False


class RuleTableError(Exception):
    """规则表加载期校验失败。"""


# ---------------------------------------------------------------- 可满足性分析

def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _field_satisfiable(conds) -> bool:
    """单字段上若干 AND 条件是否可满足。"""
    allowed = None          # 有限候选集（来自 == / in）
    lo, lo_inc = None, True
    hi, hi_inc = None, True
    exclusions = set()
    for _, op, v in conds:
        if op == "==":
            allowed = {v} if allowed is None else (allowed & {v})
        elif op == "in":
            allowed = set(v) if allowed is None else (allowed & set(v))
        elif op == "!=":
            exclusions.add(v)
        elif op in (">", ">="):
            inc = op == ">="
            if lo is None or v > lo:
                lo, lo_inc = v, inc
            elif v == lo:
                lo_inc = lo_inc and inc
        elif op in ("<", "<="):
            inc = op == "<="
            if hi is None or v < hi:
                hi, hi_inc = v, inc
            elif v == hi:
                hi_inc = hi_inc and inc
        else:
            raise ValueError(f"未知运算符: {op}")
    if allowed is not None:
        allowed = {x for x in allowed if x not in exclusions}
        if lo is not None:
            allowed = {x for x in allowed if x > lo or (lo_inc and x == lo)}
        if hi is not None:
            allowed = {x for x in allowed if x < hi or (hi_inc and x == hi)}
        return len(allowed) > 0
    if lo is not None and hi is not None:
        if lo > hi:
            return False
        if lo == hi and not (lo_inc and hi_inc):
            return False
    # 注：仅有 != 排除、无有限候选集时按可满足处理（保守假设定义域足够大）
    return True


def _satisfiable(conditions) -> bool:
    by_field: Dict[str, List[Condition]] = {}
    for cond in conditions:
        by_field.setdefault(cond[0], []).append(cond)
    return all(_field_satisfiable(cs) for cs in by_field.values())


# ---------------------------------------------------------------- 蕴含分析

def _constraint_implied(cond, conds) -> bool:
    """conds（AND）是否蕴含单个条件 cond。"""
    f, op, v = cond
    for f2, op2, v2 in conds:
        if f2 != f:
            continue
        if op2 == "==":
            if op == "==" and v2 == v:
                return True
            if op == "!=" and v2 != v:
                return True
            if op == "in" and v2 in v:
                return True
            if _is_number(v2) and _is_number(v):
                if op == ">=" and v2 >= v:
                    return True
                if op == ">" and v2 > v:
                    return True
                if op == "<=" and v2 <= v:
                    return True
                if op == "<" and v2 < v:
                    return True
        elif op2 == "in":
            s = set(v2)
            if op == "==" and v in s:
                return True
            if op == "in" and s <= set(v):
                return True
            if op == "!=" and v not in s:
                return True
            if all(_is_number(x) for x in s) and _is_number(v):
                if op == ">=" and all(x >= v for x in s):
                    return True
                if op == ">" and all(x > v for x in s):
                    return True
                if op == "<=" and all(x <= v for x in s):
                    return True
                if op == "<" and all(x < v for x in s):
                    return True
        elif op2 == "!=":
            if op == "!=" and v2 == v:
                return True
        elif op2 in (">", ">=") and _is_number(v2) and _is_number(v):
            if op == ">=" and v2 >= v:
                return True
            if op == ">" and (v2 > v or (v2 == v and op2 == ">")):
                return True
        elif op2 in ("<", "<=") and _is_number(v2) and _is_number(v):
            if op == "<=" and v2 <= v:
                return True
            if op == "<" and (v2 < v or (v2 == v and op2 == "<")):
                return True
    return False


def _implies(conds_a, conds_b) -> bool:
    """conds_a（AND）是否蕴含 conds_b（AND），即 b 比 a 更一般。"""
    return all(_constraint_implied(c, conds_a) for c in conds_b)


# ---------------------------------------------------------------- 校验

def validate(rules) -> List[str]:
    """返回问题列表（空列表表示校验通过）。"""
    issues: List[str] = []

    defaults = [r for r in rules if r.default]
    if not defaults:
        issues.append("DEFAULT: 缺少默认规则（default=True 的规则必须恰好一条）")
    elif len(defaults) > 1:
        ids = ", ".join(r.rule_id for r in defaults)
        issues.append(f"DEFAULT: 默认规则不唯一（{ids}）")

    normal = [r for r in rules if not r.default]

    for r in normal:
        if not _satisfiable(r.conditions):
            issues.append(f"UNREACHABLE {r.rule_id}: 条件自相矛盾，永不可满足")

    for a in normal:
        for b in normal:
            if a is b:
                continue
            if b.priority >= a.priority and _implies(a.conditions, b.conditions):
                issues.append(
                    f"UNREACHABLE {a.rule_id}: 被优先级更高/相等的规则 "
                    f"{b.rule_id} 完全遮蔽"
                )
                break

    for i, a in enumerate(normal):
        for b in normal[i + 1:]:
            if a.priority != b.priority:
                continue
            if a.result == b.result:
                continue
            if _satisfiable(a.conditions + b.conditions):
                issues.append(
                    f"CONFLICT {a.rule_id} <-> {b.rule_id}: "
                    f"条件重叠、优先级相同（{a.priority}）但结果不同"
                )
    return issues


# ---------------------------------------------------------------- 引擎

class Engine:
    """按优先级从高到低依次匹配；均不命中时落入显式声明的默认规则。"""

    def __init__(self, rules):
        self.rules = list(rules)
        issues = validate(self.rules)
        if issues:
            raise RuleTableError(
                "规则表校验失败:\n" + "\n".join("  - " + s for s in issues)
            )
        self._ordered = sorted(
            (r for r in self.rules if not r.default),
            key=lambda r: r.priority,
            reverse=True,
        )
        self._default = next(r for r in self.rules if r.default)

    def match(self, ctx: Dict[str, Any]) -> Rule:
        for r in self._ordered:
            if all(_OPS[op](ctx[f], v) for f, op, v in r.conditions):
                return r
        return self._default
