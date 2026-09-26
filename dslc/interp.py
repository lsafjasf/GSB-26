"""Interpreter executing a plan against concrete parameter inputs,
plus trace-equivalence checking used for differential testing."""

import operator

from .ast_nodes import Bin, Lit, Not, Unresolved
from .compiler import ELSE_LABEL
from .optimize import OPS


class InputError(Exception):
    pass


def evaluate(e, env):
    if isinstance(e, Lit):
        return e.value
    if isinstance(e, Unresolved):
        return env[e.name] if e.is_ref else e.name
    if isinstance(e, Not):
        return not evaluate(e.operand, env)
    if isinstance(e, Bin):
        if e.op == "and":
            return evaluate(e.left, env) and evaluate(e.right, env)
        if e.op == "or":
            return evaluate(e.left, env) or evaluate(e.right, env)
        return OPS[e.op](evaluate(e.left, env), evaluate(e.right, env))
    raise AssertionError("unknown expr node: {!r}".format(e))


def check_input_type(tspec, value):
    if tspec.kind == "bool":
        return isinstance(value, bool)
    if tspec.kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if tspec.kind == "str":
        return isinstance(value, str)
    if tspec.kind == "enum":
        return isinstance(value, str) and value in tspec.values
    return False


class Trace:
    """stages: list of stages, each a list of (step_name, did_run)."""

    def __init__(self, stages):
        self.stages = stages


def execute(plan, inputs=None):
    env = {}
    for name, p in plan["params"].items():
        if "default" in p:
            env[name] = p["default"]
    for k, v in (inputs or {}).items():
        if k not in plan["params"]:
            raise InputError("unknown input parameter {!r}".format(k))
        tspec = plan["params"][k]["type"]
        if not check_input_type(tspec, v):
            raise InputError(
                "input {!r} has wrong type: expected {}, got {!r}".format(
                    k, tspec, v))
        env[k] = v
    ran = set()
    fired = {}   # branch step name -> selected case label (or ELSE_LABEL)
    out = []
    for stage in plan["stages"]:
        rec = []
        for name in stage:
            s = plan["steps"][name]
            ok = all(d in ran for d in s["needs"])
            if ok:
                for c in s["conds"]:
                    if fired.get(c["branch"]) not in c["labels"]:
                        ok = False
                        break
            if ok and s["when"] is not None:
                ok = bool(evaluate(s["when"], env))
            if ok:
                ran.add(name)
                b = s["branch"]
                if b is not None:
                    v = evaluate(b["on"], env)
                    label = ELSE_LABEL
                    for lv, _t in b["cases"]:
                        if lv == v:
                            label = lv
                            break
                    fired[name] = label
            rec.append((name, ok))
        out.append(rec)
    return Trace(out)


def effectful_sequence(plan, trace):
    """Names of executed steps that carry a `run` action, in order."""
    seq = []
    for rec in trace.stages:
        for name, did_run in rec:
            if did_run and plan["steps"][name]["run"] is not None:
                seq.append(name)
    return seq


def _ancestors(plan):
    """Transitive dependency ancestors for every step of the plan."""
    preds = {}
    for n, s in plan["steps"].items():
        ps = set(d for d in s["needs"] if d in plan["steps"])
        ps.update(c["branch"] for c in s["conds"] if c["branch"] in plan["steps"])
        preds[n] = ps
    cache = {}

    def visit(n):
        if n not in cache:
            acc = set()
            for p in preds.get(n, ()):
                acc.add(p)
                acc.update(visit(p))
            cache[n] = acc
        return cache[n]

    return {n: visit(n) for n in preds}


def _respects_order(plan_ref, ancestors, plan, trace):
    """Check the trace is a linear extension of plan_ref's partial order,
    restricted to the executed effectful steps."""
    seq = effectful_sequence(plan, trace)
    pos = {n: i for i, n in enumerate(seq)}
    for name, i in pos.items():
        if name not in plan_ref["steps"]:
            return False, "step {!r} not present in reference plan".format(name)
        for a in ancestors.get(name, ()):
            if a in pos and pos[a] >= i:
                return False, (
                    "ordering violated: {!r} must precede {!r}".format(a, name))
    return True, "ok"


def traces_equivalent(plan_l, trace_l, plan_o, trace_o):
    """Semantic equivalence of an unoptimized and an optimized run.

    Returns (bool, reason). Two runs are equivalent when:
      * the sets of executed effectful steps are identical, and
      * both traces are linear extensions of the unoptimized plan's
        dependency partial order (restricted to the executed steps),
        i.e. every dependency that ran is ordered before its dependents.
    """
    seq_l = effectful_sequence(plan_l, trace_l)
    seq_o = effectful_sequence(plan_o, trace_o)
    if set(seq_l) != set(seq_o):
        return False, (
            "executed step sets differ: only in unoptimized: {}, "
            "only in optimized: {}".format(
                sorted(set(seq_l) - set(seq_o)),
                sorted(set(seq_o) - set(seq_l))))
    ancestors = _ancestors(plan_l)
    ok, why = _respects_order(plan_l, ancestors, plan_l, trace_l)
    if not ok:
        return False, "unoptimized trace: " + why
    return _respects_order(plan_l, ancestors, plan_o, trace_o)
