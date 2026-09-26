"""Compile a validated Config into an executable plan.

Plan structure (JSON-friendly except TypeSpec / expr nodes):

  plan = {
    "params": {name: {"type": TypeSpec, "default": value}},   # default optional
    "steps":  {name: {
        "run":    str | None,          # effectful action
        "needs":  [step, ...],         # unconditional dependencies (all must run)
        "conds":  [{"branch": b, "labels": [v, ...]}, ...],  # ALL must be fired
        "when":   expr | None,         # guard
        "branch": {"on": expr, "cases": [(label, target|None)],
                   "else": target|None} | None,
    }},
    "stages": [[step, ...], ...],      # execution stages
  }

The unoptimized plan has one step per stage (sequential order).
The optimized plan groups independent steps of the same topological level
into one stage (logically parallel).
"""

import heapq

from .ast_nodes import Lit, Unresolved
from .validate import validate

ELSE_LABEL = "<else>"


def node_value(node):
    if isinstance(node, Lit):
        return node.value
    return node.name  # enum literal


def case_label_value(c):
    v = getattr(c, "value", None)
    if v is None:
        v = node_value(c.label)
    return v


def dedup(xs):
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def compile_plan(config, optimize=True):
    """Validate and compile. Raises DSLErrorList on static errors."""
    validate(config)
    return build_plan(config, optimize=optimize)


def build_plan(config, optimize=True):
    """Compile an already-validated config."""
    steps = {}
    for s in config.steps:
        if s.name in steps:
            continue  # duplicate: reported by validate, keep first
        branch = None
        if s.branch is not None:
            branch = {
                "on": s.branch.on,
                "cases": [(case_label_value(c),
                           c.target[0] if c.target else None)
                          for c in s.branch.cases],
                "else": (s.branch.else_target[0]
                         if s.branch.else_target else None),
            }
        steps[s.name] = {
            "run": s.run,
            "needs": dedup([n for n, _, _ in s.needs]),
            "conds": [],
            "when": s.when,
            "branch": branch,
            "line": s.line,
        }
    # Conditional activation edges: branch -> case targets.
    for s in config.steps:
        if s.branch is None or s.name not in steps:
            continue
        labels_by_target = {}
        for c in s.branch.cases:
            if c.target is not None:
                labels_by_target.setdefault(c.target[0], []).append(
                    case_label_value(c))
        if s.branch.else_target is not None:
            labels_by_target.setdefault(s.branch.else_target[0], []).append(
                ELSE_LABEL)
        for target, labels in labels_by_target.items():
            if target in steps:
                steps[target]["conds"].append(
                    {"branch": s.name, "labels": labels})
    params = {}
    for p in config.params:
        entry = {"type": p.type}
        if p.default is not None:
            entry["default"] = node_value(p.default)
        params[p.name] = entry
    plan = {"params": params, "steps": steps}
    if optimize:
        from .optimize import optimize_plan
        optimize_plan(plan)
        plan["stages"] = leveled_stages(steps)
    else:
        order, _ = compute_order(steps)
        plan["stages"] = [[n] for n in order]
    return plan


def compute_order(steps):
    """Topological order by (level, name). level = longest path from sources."""
    preds = {n: set() for n in steps}
    for n, s in steps.items():
        for d in s["needs"]:
            if d in steps:
                preds[n].add(d)
        for c in s["conds"]:
            if c["branch"] in steps:
                preds[n].add(c["branch"])
    succ = {n: [] for n in steps}
    indeg = {}
    for n, ps in preds.items():
        indeg[n] = len(ps)
        for p in ps:
            succ[p].append(n)
    level = {n: 0 for n in steps}
    heap = [(0, n) for n in steps if indeg[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        lv, n = heapq.heappop(heap)
        order.append(n)
        for m in succ[n]:
            if lv + 1 > level[m]:
                level[m] = lv + 1
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(heap, (level[m], m))
    if len(order) != len(steps):
        raise AssertionError("cycle should have been caught by validation")
    return order, level


def leveled_stages(steps):
    order, level = compute_order(steps)
    stages = []
    for n in order:
        lv = level[n]
        while len(stages) <= lv:
            stages.append([])
        stages[lv].append(n)
    return stages
