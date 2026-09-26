"""约束生成 + 求解的类型检查器（Hindley-Milner 风格）。

- 遍历 AST 生成等式约束，交给 Solver 合一求解；
- let / 顶层定义在组边界求解后泛化，使用处再实例化（支持多态）；
- 递归与互递归（defrec）：组内名字先用单态类型变量占位，
  组内约束解完后一次性泛化——递归调用永不展开，保证终止；
- 无法推断为具体类型的变量显式报告为泛型占位，绝不默认为 Any。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .astnodes import (Ann, App, BoolLit, Def, DefRec, If, IntLit, Lam, Let,
                       ListLit, Loc, Node, Program, StrLit, Var)
from .solver import Conflict, InfiniteType, Solver
from .types import (TCon, TFun, TList, TVar, Type, free_vars,
                    occurs_in_fun_arg, prune, show)

# 深层 AST / 类型需要更大的递归预算；occurs check 等已迭代化。
sys.setrecursionlimit(max(sys.getrecursionlimit(), 100_000))


@dataclass
class Scheme:
    vars: List[TVar]
    type: Type


@dataclass
class Diagnostic:
    kind: str            # 'error' | 'warning' | 'info'
    loc: Optional[Loc]
    message: str
    chain: List[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"[{self.loc}] " if self.loc else ""
        lines = [f"{self.kind}: {head}{self.message}"]
        if self.chain:
            lines.append("  约束链:")
            for entry in self.chain:
                lines.append(f"    - {entry}")
        return "\n".join(lines)


@dataclass
class CheckResult:
    errors: List[Diagnostic]
    warnings: List[Diagnostic]
    infos: List[Diagnostic]
    def_types: Dict[str, str]
    expr_types: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def generalize(mono: Set[TVar], t: Type) -> Scheme:
    return Scheme([v for v in free_vars(t) if v not in mono], t)


def instantiate(scheme: Scheme) -> Type:
    if not scheme.vars:
        return scheme.type
    subst = {id(v): TVar(hint=v.hint, origin=v.origin) for v in scheme.vars}

    def go(t: Type) -> Type:
        t = prune(t)
        if isinstance(t, TVar):
            return subst.get(id(t), t)
        if isinstance(t, TFun):
            return TFun(go(t.arg), go(t.ret), origin=t.origin)
        if isinstance(t, TList):
            return TList(go(t.elem), origin=t.origin)
        return t

    return go(scheme.type)


def default_env() -> Dict[str, Scheme]:
    tint, tbool = TCon("Int"), TCon("Bool")

    def fn(*args: Type) -> Type:
        cur = args[-1]
        for a in reversed(args[:-1]):
            cur = TFun(a, cur)
        return cur

    a = TVar(hint="'a")
    return {
        "+": Scheme([], fn(tint, tint, tint)),
        "-": Scheme([], fn(tint, tint, tint)),
        "*": Scheme([], fn(tint, tint, tint)),
        "=": Scheme([], fn(tint, tint, tbool)),
        "not": Scheme([], fn(tbool, tbool)),
        "nil?": Scheme([a], fn(TList(a), tbool)),
        "cons": Scheme([a], fn(a, TList(a), TList(a))),
        "head": Scheme([a], fn(TList(a), a)),
        "tail": Scheme([a], fn(TList(a), TList(a))),
    }


class Checker:
    def __init__(self) -> None:
        self.solver = Solver()
        self.errors: List[Diagnostic] = []
        self.warnings: List[Diagnostic] = []
        self.infos: List[Diagnostic] = []
        self.def_types: Dict[str, str] = {}
        self.expr_types: List[str] = []

    # ---------- 顶层 ----------

    def check(self, program: Program) -> CheckResult:
        env = default_env()
        for item in program:
            if isinstance(item, Def):
                self._check_def(env, item)
            elif isinstance(item, DefRec):
                self._check_defrec(env, item)
            else:
                self._check_expr(env, item)
        self._drain(0)
        return CheckResult(self.errors, self.warnings, self.infos,
                           self.def_types, self.expr_types)

    def _check_def(self, env: Dict[str, Scheme], item: Def) -> None:
        start = self.solver.checkpoint()
        t = self.infer(env, item.expr, set())
        if item.ann is not None:
            self.solver.add(item.ann, t, f"定义 {item.name} 的类型标注与右值", item.loc)
        self._drain(start)
        env[item.name] = generalize(set(), t)
        self.def_types[item.name] = show(prune(t))
        self._ambiguity(f"定义 {item.name}", t, item.loc)

    def _check_defrec(self, env: Dict[str, Scheme], item: DefRec) -> None:
        start = self.solver.checkpoint()
        tvars = {b.name: TVar(origin=(b.loc, f"递归定义 {b.name}"))
                 for b in item.bindings}
        inner = dict(env)
        for b in item.bindings:
            inner[b.name] = Scheme([], tvars[b.name])
        mono = set(tvars.values())
        for b in item.bindings:
            ti = self.infer(inner, b.expr, mono)
            self.solver.add(tvars[b.name], ti,
                            f"递归定义 {b.name} 的名字与右值类型一致", b.loc)
            if b.ann is not None:
                self.solver.add(b.ann, tvars[b.name],
                                f"递归定义 {b.name} 的类型标注", b.loc)
        self._drain(start)
        for b in item.bindings:
            env[b.name] = generalize(set(), tvars[b.name])
            self.def_types[b.name] = show(prune(tvars[b.name]))
            self._ambiguity(f"递归定义 {b.name}", tvars[b.name], b.loc)

    def _check_expr(self, env: Dict[str, Scheme], node: Node) -> None:
        start = self.solver.checkpoint()
        t = self.infer(env, node, set())
        self._drain(start)
        self.expr_types.append(show(prune(t)))
        self._ambiguity("表达式", t, node.loc)

    # ---------- 约束生成 ----------

    def infer(self, env: Dict[str, Scheme], node: Node, mono: Set[TVar]) -> Type:
        loc = node.loc
        if isinstance(node, IntLit):
            return TCon("Int", origin=(loc, f"整数字面量 {node.value}"))
        if isinstance(node, BoolLit):
            return TCon("Bool", origin=(loc, f"布尔字面量 {node.value}"))
        if isinstance(node, StrLit):
            return TCon("Str", origin=(loc, f"字符串字面量 {node.value!r}"))
        if isinstance(node, Var):
            scheme = env.get(node.name)
            if scheme is None:
                self.errors.append(Diagnostic(
                    "error", loc, f"未绑定的变量 {node.name!r}"))
                return TVar(origin=(loc, f"未绑定变量 {node.name}"))
            t = instantiate(scheme)
            if t.origin is None:
                t.origin = (loc, f"变量 {node.name} 的使用")
            return t
        if isinstance(node, Lam):
            tv = node.ann if node.ann is not None else TVar(
                origin=(loc, f"参数 {node.param}（未标注）"))
            inner = dict(env)
            inner[node.param] = Scheme([], tv)
            body_t = self.infer(inner, node.body, mono | {tv})
            return TFun(tv, body_t, origin=(loc, f"函数表达式 (fn {node.param} ...)"))
        if isinstance(node, App):
            tf = self.infer(env, node.func, mono)
            ta = self.infer(env, node.arg, mono)
            tr = TVar(origin=(loc, "调用结果"))
            self.solver.add(
                tf, TFun(ta, tr, origin=(loc, "调用处构造函数类型")),
                "函数调用：被调表达式必须具有 (参数 -> 结果) 类型", loc)
            return tr
        if isinstance(node, If):
            tc = self.infer(env, node.cond, mono)
            self.solver.add(
                TCon("Bool", origin=(node.cond.loc, "if 条件必须是 Bool")), tc,
                "if 条件必须是 Bool", node.cond.loc)
            tt = self.infer(env, node.then, mono)
            te = self.infer(env, node.otherwise, mono)
            self.solver.add(tt, te, "if 两个分支的类型必须一致", loc)
            return tt
        if isinstance(node, Let):
            start = self.solver.checkpoint()
            tv = self.infer(env, node.value, mono)
            if node.ann is not None:
                self.solver.add(node.ann, tv,
                                f"let 绑定 {node.name} 的类型标注", node.loc)
            self._drain(start)
            inner = dict(env)
            inner[node.name] = generalize(mono, tv)
            return self.infer(inner, node.body, mono)
        if isinstance(node, Ann):
            t = self.infer(env, node.expr, mono)
            self.solver.add(node.ann, t, "类型标注 (the ...)", loc)
            return t
        if isinstance(node, ListLit):
            elem = TVar(origin=(loc, "列表元素类型"))
            for item in node.items:
                ti = self.infer(env, item, mono)
                self.solver.add(elem, ti, "列表所有元素的类型必须一致", item.loc)
            return TList(elem, origin=(loc, "列表字面量"))
        raise TypeError(f"未知节点: {node!r}")

    # ---------- 诊断 ----------

    def _drain(self, start: int) -> None:
        for exc in self.solver.solve_from(start):
            self.errors.append(self._to_diagnostic(exc))

    def _to_diagnostic(self, exc: Exception) -> Diagnostic:
        if isinstance(exc, InfiniteType):
            c = exc.constraint
            chain = [f"[{c.loc}] {c.reason}：要求 {show(prune(c.a))} = {show(prune(c.b))}"]
            return Diagnostic(
                "error", c.loc,
                f"递归类型：{show(exc.var)} 会等于 {show(exc.type)}，"
                f"需要无限展开，已拒绝（occurs check）", chain)
        assert isinstance(exc, Conflict)
        c = exc.constraint
        path = f"（位置：{exc.path}）" if exc.path else ""
        chain = [f"[{c.loc}] {c.reason}：要求 {show(prune(c.a))} = {show(prune(c.b))}"]
        for label, t in (("期望", exc.expected), ("实际", exc.actual)):
            origin = self._origin_of(t)
            if origin:
                chain.append(f"{label}类型 {show(t)} 来自 {origin}")
        return Diagnostic(
            "error", c.loc,
            f"类型不匹配{path}：期望 {show(exc.expected)}，实际 {show(exc.actual)}",
            chain)

    @staticmethod
    def _origin_of(t: Type) -> Optional[str]:
        t = prune(t)
        origin = getattr(t, "origin", None)
        if origin:
            return f"{origin[1]}（{origin[0]}）"
        return None

    def _ambiguity(self, what: str, t: Type, loc: Loc) -> None:
        t = prune(t)
        for v in free_vars(t):
            if occurs_in_fun_arg(t, v):
                self.infos.append(Diagnostic(
                    "info", loc,
                    f"{what}：{show(v)} 为合法的多态泛型占位"
                    f"（类型 {show(t)}），未默认为 Any"))
            else:
                self.warnings.append(Diagnostic(
                    "warning", loc,
                    f"{what}：{show(v)} 无法推断为具体类型"
                    f"（类型 {show(t)}），保留为泛型占位，未默认为 Any"))
