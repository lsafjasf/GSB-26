"""Constraint-generating type checker (Hindley-Milner style).

Pipeline per expression:
  1. generate a constraint for every local typing fact (Checker.unify),
  2. solve it immediately with the union-find unifier (solver.Unifier),
  3. generalise at let / letrec / top-level boundaries (let-polymorphism).

Recursive and mutually recursive definitions are handled by pre-binding
every name in the group to a fresh type variable *before* checking the
bodies, so recursive references never unfold anything and the solver
always terminates (see README, "Termination").
"""
import sys

from .ast_nodes import BoolLit, Call, If, IntLit, Lam, Let, LetRec, StrLit, Var
from .errors import Diagnostic, TypeCheckError
from .solver import ConflictError, OccursError, Unifier
from .types import (BOOL, CONSTANTS, INT, STR, QVar, Scheme, TFun, TVar,
                    format_type, free_tvars, occurs, prune)


def _copy(t, mapping):
    t = prune(t)
    if isinstance(t, (TVar, QVar)):
        return mapping.get(t, t)
    if isinstance(t, TFun):
        return TFun(_copy(t.arg, mapping), _copy(t.ret, mapping))
    return t


def _tokenize_ann(text):
    tokens = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append(ch)
            i += 1
        elif text.startswith("->", i):
            tokens.append("->")
            i += 2
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            raise ValueError(f"bad character {ch!r} in annotation {text!r}")
    return tokens


def parse_annotation(text, fresh):
    """Parse e.g. ``int -> int`` or ``(a -> b) -> a -> b``.
    Unknown lowercase names are generic placeholders, shared within one
    annotation."""
    tokens = _tokenize_ann(text)
    pos = [0]
    placeholders = {}

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def parse_type():
        left = parse_atom()
        if peek() == "->":
            pos[0] += 1
            return TFun(left, parse_type())
        return left

    def parse_atom():
        tok = peek()
        if tok == "(":
            pos[0] += 1
            t = parse_type()
            if peek() != ")":
                raise ValueError(f"unbalanced '(' in annotation {text!r}")
            pos[0] += 1
            return t
        if tok is None or tok in (")", "->"):
            raise ValueError(f"unexpected token in annotation {text!r}")
        pos[0] += 1
        if tok in CONSTANTS:
            return CONSTANTS[tok]
        if tok not in placeholders:
            placeholders[tok] = fresh(tok)
        return placeholders[tok]

    result = parse_type()
    if pos[0] != len(tokens):
        raise ValueError(f"trailing tokens in annotation {text!r}")
    return result


def _fun(*ts):
    t = ts[-1]
    for x in reversed(ts[:-1]):
        t = TFun(x, t)
    return t


def builtins():
    a = QVar(-1, "a")
    return {
        "add": Scheme([], _fun(INT, INT, INT), closed=True),
        "sub": Scheme([], _fun(INT, INT, INT), closed=True),
        "mul": Scheme([], _fun(INT, INT, INT), closed=True),
        "neg": Scheme([], _fun(INT, INT), closed=True),
        "lt": Scheme([], _fun(INT, INT, BOOL), closed=True),
        "not": Scheme([], _fun(BOOL, BOOL), closed=True),
        "concat": Scheme([], _fun(STR, STR, STR), closed=True),
        "show": Scheme([], _fun(INT, STR), closed=True),
        "eq": Scheme([a], _fun(a, a, BOOL), closed=True),
    }


class CheckResult:
    def __init__(self, env, main_type, diagnostics, unifier):
        self.env = env                    # name -> Scheme (builtins + top-level defs)
        self.main_type = main_type        # Type of the main expression, or None
        self.diagnostics = diagnostics    # non-fatal findings (e.g. uninferred params)
        self.constraint_count = len(unifier.constraints)
        self.solver_steps = unifier.steps

    def format_main(self):
        return None if self.main_type is None else format_type(self.main_type)


class Checker:
    def __init__(self, max_steps=2_000_000):
        self.unifier = Unifier(max_steps)
        self.next_var = 0
        self.lambda_params = []  # (node, tvar, body_type) for the uninferred check
        self.diagnostics = []

    def fresh(self, hint=""):
        var = TVar(self.next_var, hint)
        self.next_var += 1
        return var

    def unify(self, left, right, reason, node):
        try:
            return self.unifier.unify(left, right, reason, node)
        except ConflictError as err:
            chain = self.unifier.chain_for(err.left, err.right, err.constraint)
            raise TypeCheckError(Diagnostic(
                kind="conflict",
                pos=err.constraint.node.pos,
                message=f"type mismatch ({err.constraint.reason})",
                expected=format_type(err.left),
                actual=format_type(err.right),
                chain=chain)) from None
        except OccursError as err:
            chain = self.unifier.chain_for(err.var, err.into, err.constraint)
            raise TypeCheckError(Diagnostic(
                kind="occurs",
                pos=err.constraint.node.pos,
                message="recursive value would have an infinite type",
                expected=format_type(err.var),
                actual=format_type(err.into),
                chain=chain)) from None

    def instantiate(self, scheme):
        if not scheme.qvars:
            return scheme.type
        mapping = {q: self.fresh(q.hint) for q in scheme.qvars}
        return _copy(scheme.type, mapping)

    def generalize(self, env, t):
        env_free = []
        for scheme in env.values():
            if scheme.closed:
                continue
            free_tvars(scheme.type, env_free)
        mapping = {}
        qvars = []
        for var in free_tvars(t):
            if all(var is not other for other in env_free):
                qvar = QVar(var.vid, var.hint)
                mapping[var] = qvar
                qvars.append(qvar)
        copied = _copy(t, mapping)
        closed = not free_tvars(copied)
        return Scheme(qvars, copied, closed)

    def infer(self, env, node):
        if isinstance(node, IntLit):
            return INT
        if isinstance(node, BoolLit):
            return BOOL
        if isinstance(node, StrLit):
            return STR
        if isinstance(node, Var):
            scheme = env.get(node.name)
            if scheme is None:
                raise TypeCheckError(Diagnostic(
                    kind="unbound", pos=node.pos,
                    message=f"unbound variable {node.name!r}"))
            return self.instantiate(scheme)
        if isinstance(node, Lam):
            tv = self.fresh(node.param)
            if node.ann is not None:
                ann_t = parse_annotation(node.ann, self.fresh)
                self.unify(tv, ann_t,
                           f"annotation of parameter {node.param!r}", node)
            body_env = dict(env)
            body_env[node.param] = Scheme([], tv)
            body_t = self.infer(body_env, node.body)
            self.lambda_params.append((node, tv, body_t))
            return TFun(tv, body_t)
        if isinstance(node, Call):
            fn_t = self.infer(env, node.fn)
            arg_t = self.infer(env, node.arg)
            ret = self.fresh("ret")
            self.unify(fn_t, TFun(arg_t, ret), "function application", node)
            return ret
        if isinstance(node, If):
            cond_t = self.infer(env, node.cond)
            self.unify(cond_t, BOOL, "if condition must be bool", node.cond)
            then_t = self.infer(env, node.then)
            else_t = self.infer(env, node.otherwise)
            self.unify(then_t, else_t, "if branches must agree", node)
            return then_t
        if isinstance(node, Let):
            value_t = self.infer(env, node.value)
            if node.ann is not None:
                ann_t = parse_annotation(node.ann, self.fresh)
                self.unify(ann_t, value_t, f"annotation of {node.name!r}", node)
                value_t = ann_t
            scheme = self.generalize(env, value_t)
            body_env = dict(env)
            body_env[node.name] = scheme
            return self.infer(body_env, node.body)
        if isinstance(node, LetRec):
            _, body_t = self.infer_rec(env, node.bindings, node.body)
            return body_t
        raise TypeError(f"unknown node {node!r}")

    def infer_rec(self, env, bindings, body):
        """Check one mutually recursive group. Names are pre-bound to fresh
        variables so recursive references add O(1) constraints and nothing
        is ever unfolded."""
        pre = {}
        rec_env = dict(env)
        for b in bindings:
            tv = self.fresh(b.name)
            if b.ann is not None:
                ann_t = parse_annotation(b.ann, self.fresh)
                self.unify(tv, ann_t, f"annotation of {b.name!r}", b.value)
            pre[b.name] = tv
            rec_env[b.name] = Scheme([], tv)
        for b in bindings:
            value_t = self.infer(rec_env, b.value)
            self.unify(pre[b.name], value_t,
                       f"recursive binding {b.name!r}", b.value)
        schemes = {b.name: self.generalize(env, pre[b.name]) for b in bindings}
        if body is None:
            return schemes, None
        rec_env.update(schemes)
        return schemes, self.infer(rec_env, body)

    def check_uninferred(self):
        """Report lambda parameters whose type stayed a free variable that
        does not even flow into the result: those are genuinely
        uninferable and must not pass silently as 'any'."""
        for node, tv, body_t in self.lambda_params:
            if prune(tv) is tv and not occurs(tv, body_t):
                self.diagnostics.append(Diagnostic(
                    kind="uninferred", pos=node.pos,
                    message=f"cannot infer the type of parameter "
                            f"{node.param!r}; add an annotation"))


def check_program(program, max_steps=2_000_000):
    if sys.getrecursionlimit() < 100_000:
        sys.setrecursionlimit(100_000)
    checker = Checker(max_steps)
    env = builtins()
    for group in program.groups:
        schemes, _ = checker.infer_rec(env, group, None)
        env.update(schemes)
    main_type = None
    if program.main is not None:
        main_type = checker.infer(env, program.main)
    checker.check_uninferred()
    return CheckResult(env, main_type, checker.diagnostics, checker.unifier)
