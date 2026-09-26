"""Plan optimizations. Each rule preserves the observable semantics
(the set of effectful steps executed and their partial order); see README.

Rules:
  1. Constant-fold `when` guards. `when: true` is dropped; `when: false`
     marks the step dead (it can never run).
  2. Dead-step removal: a dead step that nothing references (no dependent,
     not a branch target, not a condition source) is removed.
  3. No-op elimination: a step with no action, no guard, and no effective
     branch is removed; its dependencies and activation conditions are
     forwarded to its dependents, and branch cases pointing at it become
     `skip`. A branch with no targets left is itself a no-op, but is only
     removed when no activation condition references it.
  4. Parallel merge: steps of the same topological level are merged into
     one stage (done by the compiler via leveled_stages).
"""

import operator

from .ast_nodes import BOOL, Bin, Lit, Not, Unresolved

OPS = {
    "==": operator.eq, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le,
    ">": operator.gt, ">=": operator.ge,
}


def const_value(e):
    """(True, value) if e evaluates without any parameter, else (False, None)."""
    if isinstance(e, Lit):
        return True, e.value
    if isinstance(e, Unresolved):
        if e.is_ref:
            return False, None
        return True, e.name  # enum literal is a constant
    if isinstance(e, Not):
        ok, v = const_value(e.operand)
        return (True, not v) if ok else (False, None)
    if isinstance(e, Bin):
        ok1, v1 = const_value(e.left)
        ok2, v2 = const_value(e.right)
        if not (ok1 and ok2):
            return False, None
        if e.op == "and":
            return True, bool(v1 and v2)
        if e.op == "or":
            return True, bool(v1 or v2)
        return True, OPS[e.op](v1, v2)
    return False, None


def optimize_plan(plan):
    steps = plan["steps"]
    # Rule 1: constant-fold guards.
    for s in steps.values():
        if s["when"] is not None:
            ok, v = const_value(s["when"])
            if ok:
                s["when"] = None
                if not v:
                    s["dead"] = True
    changed = True
    while changed:
        changed = False
        cond_refs = set()
        referenced = set()
        dependents = {}   # step -> set of steps listing it in `needs`
        targeters = {}    # step -> set of branch steps targeting it
        for n, s in steps.items():
            for d in s["needs"]:
                referenced.add(d)
                dependents.setdefault(d, set()).add(n)
            for c in s["conds"]:
                cond_refs.add(c["branch"])
            if s["branch"]:
                for _, t in s["branch"]["cases"]:
                    if t:
                        referenced.add(t)
                        targeters.setdefault(t, set()).add(n)
                if s["branch"]["else"]:
                    referenced.add(s["branch"]["else"])
                    targeters.setdefault(s["branch"]["else"], set()).add(n)
        referenced.update(cond_refs)
        # Rule 2: remove dead steps nothing references.
        for n in list(steps):
            if steps[n].get("dead") and n not in referenced:
                del steps[n]
                changed = True
        # Rule 3: eliminate no-op steps.
        for n in list(steps):
            s = steps.get(n)
            if s is None:
                continue
            if s["run"] is not None or s["when"] is not None or s.get("dead"):
                continue
            if s["branch"] is not None:
                has_targets = any(t for _, t in s["branch"]["cases"]) \
                    or s["branch"]["else"]
                if has_targets or n in cond_refs:
                    continue
            rewire(steps, n, dependents, targeters)
            del steps[n]
            changed = True
    for s in steps.values():
        if s.pop("dead", None):
            # Dead but still referenced: keep it with a never-true guard so
            # it (and everything gated behind it) stays skipped at runtime.
            s["when"] = Lit(False, BOOL, 0, 0)


def rewire(steps, n, dependents, targeters):
    """Splice no-op step ``n`` out of the graph, preserving activation logic.

    Only touches the actual dependents / targeting branches of ``n`` (via the
    indexes), so one elimination costs O(out-degree) instead of O(#steps).
    """
    s = steps[n]
    for m in dependents.get(n, ()):
        d = steps.get(m)
        if d is None or n not in d["needs"]:
            continue
        d["needs"] = [x for x in d["needs"] if x != n]
        for x in s["needs"]:
            if x not in d["needs"] and x != m:
                d["needs"].append(x)
                dependents.setdefault(x, set()).add(m)
        for c in s["conds"]:
            newc = {"branch": c["branch"], "labels": list(c["labels"])}
            if newc not in d["conds"]:
                d["conds"].append(newc)
    for b in targeters.get(n, ()):
        d = steps.get(b)
        if d is None or not d["branch"]:
            continue
        d["branch"]["cases"] = [
            (l, None if t == n else t) for l, t in d["branch"]["cases"]]
        if d["branch"]["else"] == n:
            d["branch"]["else"] = None
    dependents.pop(n, None)
    targeters.pop(n, None)
