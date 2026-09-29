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
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------- 判定追踪

# 评估状态：
#   hit                 首个命中（实际生效）
#   not_matched         在命中之前被评估，条件不满足
#   skipped_by_priority 条件也满足，但优先级低于已命中规则，被跳过
#   not_reached         排在命中之后，实际匹配中不会被评估到
STATUS_LABELS = {
    "hit": "命中",
    "not_matched": "未命中",
    "skipped_by_priority": "被优先级跳过",
    "not_reached": "未评估到（已被截断）",
}


@dataclass(frozen=True)
class ConditionCheck:
    """单条条件的求值结果。"""
    field: str
    op: str
    expected: Any
    actual: Any
    passed: bool

    def render(self) -> str:
        mark = "✓" if self.passed else "✗"
        return (f"{mark} {self.field} {self.op} {self.expected!r}"
                f"（实际值 {self.actual!r}）")


@dataclass(frozen=True)
class RuleEvaluation:
    """一条规则在一次判定中的评估记录。"""
    rule_id: str
    priority: int
    matched: bool
    status: str
    checks: Tuple[ConditionCheck, ...]

    def first_failure(self) -> Optional[ConditionCheck]:
        return next((c for c in self.checks if not c.passed), None)


@dataclass(frozen=True)
class Trace:
    """一次判定的完整来源追踪。

    rule     实际生效的规则（命中规则；未命中任何业务规则时为默认规则）
    matched  命中的业务规则；落入默认分支时为 None
    """
    ctx: Dict[str, Any]
    evaluations: Tuple[RuleEvaluation, ...]
    rule: Rule
    matched: Optional[Rule]

    @property
    def is_default(self) -> bool:
        return self.matched is None

    @property
    def skipped_by_priority(self) -> Tuple[str, ...]:
        """条件同样满足、但因优先级低于命中规则而被跳过的规则编号。"""
        return tuple(e.rule_id for e in self.evaluations
                     if e.status == "skipped_by_priority")

    def _result_display(self) -> str:
        result = self.rule.result
        if isinstance(result, tuple) and len(result) == 2 and callable(result[1]):
            return repr((result[0], result[1](self.ctx)))
        return repr(result)

    def render(self) -> str:
        """渲染为可读文本报告（单个判定）。"""
        lines = []
        ctx_str = ", ".join(f"{k}={v!r}" for k, v in self.ctx.items())
        lines.append(f"输入: {ctx_str}")
        lines.append("评估过程（按优先级降序）:")
        not_reached = 0
        for e in self.evaluations:
            if e.status == "not_reached":
                not_reached += 1
                continue
            lines.append(f"  [{e.rule_id}] (priority={e.priority}) "
                         f"{STATUS_LABELS[e.status]}")
            if e.status == "hit":
                for c in e.checks:
                    lines.append(f"      {c.render()}")
            elif e.status == "not_matched":
                lines.append(f"      首个不满足条件: "
                             f"{e.first_failure().render()}")
            elif e.status == "skipped_by_priority":
                lines.append(f"      条件全部满足，但优先级低于 "
                             f"{self.matched.rule_id}，被跳过")
        if not_reached:
            lines.append(f"  …其余 {not_reached} 条规则排在命中规则之后，"
                         f"未参与评估")
        if self.is_default:
            lines.append(
                f"结论: 默认分支 [{self.rule.rule_id}] 生效 -> "
                f"{self._result_display()}"
            )
            lines.append(f"默认分支生效原因: {len(self.evaluations)} "
                         f"条业务规则均未命中（见上）")
        else:
            skipped = self.skipped_by_priority
            extra = (f"；{', '.join(skipped)} 条件同样满足但被优先级跳过"
                     if skipped else "")
            lines.append(
                f"结论: 规则 [{self.rule.rule_id}] 命中 -> "
                f"{self._result_display()}{extra}"
            )
        return "\n".join(lines)


def render_report(traces, title: str = "判定来源追踪报告") -> str:
    """把多次判定的追踪结果渲染为一份可读报告。"""
    traces = list(traces)
    parts = [f"# {title}", "", f"共 {len(traces)} 次判定。"]
    for i, t in enumerate(traces, 1):
        parts += ["", f"## 判定 {i}", "", t.render()]
    return "\n".join(parts) + "\n"


def export_report(traces, path, title: str = "判定来源追踪报告") -> str:
    """渲染报告并写入文件，返回文件路径。"""
    text = render_report(traces, title=title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return str(path)


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

    def explain(self, ctx: Dict[str, Any]) -> Trace:
        """对一次判定做来源追踪。

        与 match 使用同一份规则表（self._ordered）与同一组运算符（_OPS），
        首个命中规则即 match 的返回结果；其余规则继续评估以解释
        “哪些规则因优先级被跳过 / 默认分支为何生效”。
        """
        evaluations: List[RuleEvaluation] = []
        matched: Optional[Rule] = None
        for r in self._ordered:
            checks = tuple(
                ConditionCheck(f, op, v, ctx[f], bool(_OPS[op](ctx[f], v)))
                for f, op, v in r.conditions
            )
            ok = all(c.passed for c in checks)
            if matched is None:
                status = "hit" if ok else "not_matched"
                if ok:
                    matched = r
            else:
                status = "skipped_by_priority" if ok else "not_reached"
            evaluations.append(
                RuleEvaluation(r.rule_id, r.priority, ok, status, checks)
            )
        rule = matched if matched is not None else self._default
        return Trace(dict(ctx), tuple(evaluations), rule, matched)
