"""Static validation: references, types, branch coverage, cycles.

Collects every error (with line:col positions) and raises DSLErrorList.
"""

from .ast_nodes import BOOL, ENUMLIT, Bin, Lit, Not, Unresolved
from .errors import DSLError, DSLErrorList


def validate(config):
    errors = []
    params = {}
    for p in config.params:
        if p.name in params:
            first = params[p.name]
            errors.append(DSLError(
                "duplicate parameter {!r} (first defined at {}:{})".format(
                    p.name, first.line, first.col), p.line, p.col))
        else:
            params[p.name] = p
    ptypes = {n: p.type for n, p in params.items()}
    for p in params.values():
        if p.default is not None:
            check_value(p.default, p.type,
                        "default of parameter {!r}".format(p.name), ptypes, errors)
    steps = {}
    for s in config.steps:
        if s.name in steps:
            first = steps[s.name]
            errors.append(DSLError(
                "duplicate step name {!r} (first defined at {}:{})".format(
                    s.name, first.line, first.col), s.line, s.col))
        else:
            steps[s.name] = s
    for s in config.steps:
        check_step(s, steps, ptypes, errors)
    if not errors:
        check_cycles(config, steps, errors)
    if errors:
        raise DSLErrorList(errors)


def check_step(s, steps, ptypes, errors):
    for key, val, ln, co in s.args:
        if key not in ptypes:
            errors.append(DSLError(
                "step {!r}: 'args' references undefined parameter {!r}".format(
                    s.name, key), ln, co))
        else:
            check_value(val, ptypes[key],
                        "argument {!r} of step {!r}".format(key, s.name),
                        ptypes, errors)
    if s.when is not None:
        t = typecheck(s.when, ptypes, errors)
        if t is ENUMLIT and isinstance(s.when, Unresolved):
            errors.append(DSLError(
                "step {!r}: 'when' references undefined parameter {!r}".format(
                    s.name, s.when.name), s.when.line, s.when.col))
        elif t is not None and t != BOOL:
            errors.append(DSLError(
                "step {!r}: 'when' must be of type bool, got {}".format(
                    s.name, t), s.when.line, s.when.col))
    for n, ln, co in s.needs:
        if n not in steps:
            errors.append(DSLError(
                "step {!r} depends on undefined step {!r}".format(s.name, n),
                ln, co))
    if s.branch is not None:
        check_branch(s, steps, ptypes, errors)


def check_branch(s, steps, ptypes, errors):
    b = s.branch
    t_on = typecheck(b.on, ptypes, errors)
    if t_on is ENUMLIT:
        errors.append(DSLError(
            "step {!r}: cannot infer the type of the branch expression".format(
                s.name), b.on.line, b.on.col))
        t_on = None
    seen = set()
    for c in b.cases:
        label = c.label
        ok = True
        val = None
        if isinstance(label, Lit):
            if t_on is not None and label.type != t_on:
                errors.append(DSLError(
                    "step {!r}: case label of type {} does not match branch "
                    "type {}".format(s.name, label.type, t_on),
                    label.line, label.col))
                ok = False
            val = label.value
        else:  # Unresolved: must be an enum literal of the branch type
            if label.name in ptypes:
                errors.append(DSLError(
                    "step {!r}: case label must be a literal, not parameter "
                    "{!r}".format(s.name, label.name), label.line, label.col))
                ok = False
            elif t_on is not None and t_on.kind == "enum":
                if label.name not in t_on.values:
                    errors.append(DSLError(
                        "step {!r}: {!r} is not a value of {}".format(
                            s.name, label.name, t_on), label.line, label.col))
                    ok = False
                val = label.name
            elif t_on is not None:
                errors.append(DSLError(
                    "step {!r}: unknown enum value or unquoted string "
                    "{!r}".format(s.name, label.name), label.line, label.col))
                ok = False
        if ok:
            c.value = val
            if val in seen:
                errors.append(DSLError(
                    "step {!r}: duplicate case label {!r}".format(s.name, val),
                    c.line, c.col))
            seen.add(val)
        if c.target is not None:
            tn, tl, tc = c.target
            if tn not in steps:
                errors.append(DSLError(
                    "step {!r}: branch targets undefined step {!r}".format(
                        s.name, tn), tl, tc))
    if b.else_target is not None:
        tn, tl, tc = b.else_target
        if tn not in steps:
            errors.append(DSLError(
                "step {!r}: branch targets undefined step {!r}".format(
                    s.name, tn), tl, tc))
    if t_on is None or b.has_else:
        return
    if t_on.kind == "enum":
        missing = [v for v in t_on.values if v not in seen]
        if missing:
            errors.append(DSLError(
                "step {!r}: branch on {} is not exhaustive: missing cases for "
                "{} and no 'else'".format(s.name, t_on,
                                          ", ".join(repr(m) for m in missing)),
                b.line, b.col))
    elif t_on.kind == "bool":
        missing = [v for v in (True, False) if v not in seen]
        if missing:
            errors.append(DSLError(
                "step {!r}: branch on bool is not exhaustive: missing cases "
                "for {} and no 'else'".format(
                    s.name, ", ".join(repr(m) for m in missing)),
                b.line, b.col))
    else:
        errors.append(DSLError(
            "step {!r}: branch on type {} requires an 'else' case".format(
                s.name, t_on), b.line, b.col))


def check_value(node, expected, what, ptypes, errors):
    if isinstance(node, Lit):
        if node.type != expected:
            errors.append(DSLError(
                "{}: expected {}, got {}".format(what, expected, node.type),
                node.line, node.col))
    elif isinstance(node, Unresolved):
        if node.name in ptypes:
            node.is_ref = True
            if ptypes[node.name] != expected:
                errors.append(DSLError(
                    "{}: expected {}, got {} (parameter {!r})".format(
                        what, expected, ptypes[node.name], node.name),
                    node.line, node.col))
        else:
            if expected.kind == "enum" and node.name in expected.values:
                pass  # enum literal of the expected type
            elif expected.kind == "enum":
                errors.append(DSLError(
                    "{}: {!r} is not a value of {}".format(
                        what, node.name, expected), node.line, node.col))
            else:
                errors.append(DSLError(
                    "{}: undefined parameter or unknown value {!r}".format(
                        what, node.name), node.line, node.col))


def typecheck(e, ptypes, errors):
    """Returns the TypeSpec of e, or None if an error was reported."""
    if isinstance(e, Lit):
        return e.type
    if isinstance(e, Unresolved):
        if e.name in ptypes:
            e.is_ref = True
            return ptypes[e.name]
        return ENUMLIT
    if isinstance(e, Not):
        t = typecheck(e.operand, ptypes, errors)
        if t is not None and t != BOOL:
            errors.append(DSLError(
                "'not' requires a bool operand, got {}".format(t),
                e.line, e.col))
            return None
        return BOOL
    if isinstance(e, Bin):
        if e.op in ("and", "or"):
            lt = typecheck(e.left, ptypes, errors)
            rt = typecheck(e.right, ptypes, errors)
            bad = None
            if lt is not None and lt != BOOL:
                bad = lt
            elif rt is not None and rt != BOOL:
                bad = rt
            if bad is not None:
                errors.append(DSLError(
                    "{!r} requires bool operands, got {}".format(e.op, bad),
                    e.line, e.col))
                return None
            return BOOL
        lt = typecheck(e.left, ptypes, errors)
        rt = typecheck(e.right, ptypes, errors)
        if lt is None or rt is None:
            return None
        unified = unify(e.left, lt, e.right, rt, errors)
        if unified is None:
            return None
        lt, rt = unified
        if e.op in ("==", "!="):
            if lt != rt:
                errors.append(DSLError(
                    "cannot compare {} with {}".format(lt, rt), e.line, e.col))
                return None
        else:
            if lt != rt or lt.kind not in ("int", "str"):
                errors.append(DSLError(
                    "operator {!r} requires two ints or two strings, got {} "
                    "and {}".format(e.op, lt, rt), e.line, e.col))
                return None
        return BOOL
    raise AssertionError("unknown expr node: {!r}".format(e))


def unify(le, lt, re, rt, errors):
    """Resolve enum literals against the other operand's enum type."""
    if lt is ENUMLIT and rt is ENUMLIT:
        errors.append(DSLError(
            "cannot infer the enum type of {!r} and {!r}".format(
                le.name, re.name), le.line, le.col))
        return None
    if lt is ENUMLIT:
        if rt.kind != "enum":
            errors.append(DSLError(
                "undefined parameter or unknown value {!r}".format(le.name),
                le.line, le.col))
            return None
        if le.name not in rt.values:
            errors.append(DSLError(
                "{!r} is not a value of {}".format(le.name, rt),
                le.line, le.col))
            return None
        return rt, rt
    if rt is ENUMLIT:
        if lt.kind != "enum":
            errors.append(DSLError(
                "undefined parameter or unknown value {!r}".format(re.name),
                re.line, re.col))
            return None
        if re.name not in lt.values:
            errors.append(DSLError(
                "{!r} is not a value of {}".format(re.name, lt),
                re.line, re.col))
            return None
        return lt, lt
    return lt, rt


def check_cycles(config, steps, errors):
    # Edge direction: step -> the steps it depends on.
    edges = {name: [] for name in steps}
    for s in config.steps:
        if s.name not in edges:
            continue  # duplicate name, already reported
        for n, _, _ in s.needs:
            if n in steps:
                edges[s.name].append(n)
        if s.branch is not None:
            for c in s.branch.cases:
                if c.target is not None and c.target[0] in steps:
                    edges[c.target[0]].append(s.name)
            if s.branch.else_target is not None and \
                    s.branch.else_target[0] in steps:
                edges[s.branch.else_target[0]].append(s.name)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in edges}
    for start in edges:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack = [(start, iter(edges[start]))]
        path = [start]
        while stack:
            node, it = stack[-1]
            found = None
            advanced = False
            for nb in it:
                if color[nb] == WHITE:
                    color[nb] = GRAY
                    stack.append((nb, iter(edges[nb])))
                    path.append(nb)
                    advanced = True
                    break
                elif color[nb] == GRAY:
                    found = nb
                    break
            if found is not None:
                idx = path.index(found)
                cyc = path[idx:] + [found]
                at = steps[found]
                errors.append(DSLError(
                    "dependency cycle detected: " + " -> ".join(cyc),
                    at.line, at.col))
                return
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()
