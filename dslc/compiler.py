"""把 DSL 声明编译为执行计划，并完成全部静态校验。

计划结构（可 JSON 序列化）::

    {
      "params": {name: {"type", "enum", "default"?}},
      "steps":  {name: {
                    "index", "line", "needs": [...],
                    "args": {key: ["lit", v] | ["ref", p]},
                    "guards": [[branch_step, case_label], ...],
                    "branch": {"cond": p, "cases": {label: [steps]}} | None,
                }},
      "stages": [[step, ...], ...]   # 未优化时每阶段只有一个步骤
    }
"""

import heapq

from .parser import parse
from .errors import CompileError


def value_matches_type(ptype, enum_values, value):
    if ptype == "bool":
        return isinstance(value, bool)
    if ptype == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if ptype == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ptype == "str":
        return isinstance(value, str)
    if ptype == "enum":
        return isinstance(value, str) and value in (enum_values or ())
    return False


def _check_arg_refs(value, param_table, step_name):
    if value.kind == "ref":
        if value.value not in param_table:
            raise CompileError(
                f"step {step_name!r} references unknown parameter ${value.value}",
                value.line,
                value.col,
            )
    elif value.kind == "lit" and isinstance(value.value, list):
        for item in value.value:
            _check_arg_refs(item, param_table, step_name)


def _build_graph(step_table):
    """显式 needs 边 + 分支步骤到各 case 成员的隐式边（分支必须先执行）。"""
    deps = {name: set() for name in step_table}
    for step in step_table.values():
        for dep, _, _ in step.needs:
            deps[step.name].add(dep)
        if step.branch:
            for case in step.branch.cases:
                for member in case.members:
                    deps[member].add(step.name)
    return deps


def _topological_order(step_table, deps):
    """Kahn 拓扑排序，声明顺序作为稳定的并列打破规则。"""
    indegree = {name: len(preds) for name, preds in deps.items()}
    successors = {name: [] for name in step_table}
    for name, preds in deps.items():
        for pred in preds:
            successors[pred].append(name)
    ready = []
    for name, step in step_table.items():
        if indegree[name] == 0:
            heapq.heappush(ready, (step.index, name))
    order = []
    while ready:
        _, name = heapq.heappop(ready)
        order.append(name)
        for succ in successors[name]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                heapq.heappush(ready, (step_table[succ].index, succ))
    if len(order) != len(step_table):
        remaining = {n for n in step_table if n not in set(order)}
        start = min(remaining, key=lambda n: step_table[n].index)
        path = [start]
        node = start
        while True:
            nxts = [d for d in deps[node] if d in remaining]
            node = min(nxts, key=lambda n: step_table[n].index)
            if node == start or node in path:
                path.append(node)
                break
            path.append(node)
        chain = " -> ".join(path)
        bad = step_table[path[0]]
        raise CompileError(f"cyclic dependency detected: {chain}", bad.line, bad.col)
    return order


def build_plan(params, steps):
    param_table = {}
    for param in params:
        if param.name in param_table:
            raise CompileError(
                f"duplicate parameter name {param.name!r}", param.line, param.col
            )
        if param.has_default and not value_matches_type(
            param.ptype, param.enum_values, param.default
        ):
            expected = param.ptype
            if param.ptype == "enum":
                expected = f"enum({', '.join(param.enum_values)})"
            raise CompileError(
                f"parameter {param.name!r} declared {expected} but default value "
                f"{param.default!r} is not compatible",
                param.line,
                param.col,
            )
        param_table[param.name] = param

    step_table = {}
    for step in steps:
        if step.name in step_table:
            raise CompileError(
                f"duplicate step name {step.name!r}", step.line, step.col
            )
        if step.name in param_table:
            raise CompileError(
                f"step name {step.name!r} conflicts with a parameter of the same name",
                step.line,
                step.col,
            )
        step_table[step.name] = step

    # 引用校验：needs 必须指向已定义步骤；args 里的 $ref 必须指向已定义参数。
    for step in steps:
        seen_needs = set()
        for dep, line, col in step.needs:
            if dep not in step_table:
                raise CompileError(
                    f"step {step.name!r} depends on unknown step {dep!r}", line, col
                )
            if dep == step.name:
                raise CompileError(
                    f"step {step.name!r} depends on itself", line, col
                )
            if dep in seen_needs:
                raise CompileError(
                    f"step {step.name!r} lists duplicate dependency {dep!r}", line, col
                )
            seen_needs.add(dep)
        for value in step.args.values():
            _check_arg_refs(value, param_table, step.name)

    # 条件分支校验：条件类型、case 标签合法性、覆盖完整性、成员引用存在性。
    for step in steps:
        branch = step.branch
        if branch is None:
            continue
        if branch.cond not in param_table:
            raise CompileError(
                f"step {step.name!r} branches on unknown parameter ${branch.cond}",
                branch.line,
                branch.col,
            )
        cond_param = param_table[branch.cond]
        if cond_param.ptype not in ("bool", "enum"):
            raise CompileError(
                f"step {step.name!r} branch condition ${branch.cond} must be bool or "
                f"enum, got {cond_param.ptype}",
                branch.line,
                branch.col,
            )
        labels = set()
        for case in branch.cases:
            if case.label != "_":
                if cond_param.ptype == "bool" and case.label not in ("true", "false"):
                    raise CompileError(
                        f"invalid case label {case.label!r} for bool condition "
                        f"(expected true/false)",
                        case.line,
                        case.col,
                    )
                if cond_param.ptype == "enum" and case.label not in cond_param.enum_values:
                    raise CompileError(
                        f"invalid case label {case.label!r} for enum condition "
                        f"(expected one of {', '.join(cond_param.enum_values)})",
                        case.line,
                        case.col,
                    )
            labels.add(case.label)
            for member in case.members:
                if member not in step_table:
                    raise CompileError(
                        f"step {step.name!r} case {case.label!r} references unknown "
                        f"step {member!r}",
                        case.line,
                        case.col,
                    )
        if "_" not in labels:
            if cond_param.ptype == "bool":
                missing = [x for x in ("true", "false") if x not in labels]
            else:
                missing = [x for x in cond_param.enum_values if x not in labels]
            if missing:
                raise CompileError(
                    f"branch on ${branch.cond} in step {step.name!r} is not exhaustive: "
                    f"missing {', '.join(missing)} (or add a '_' default case)",
                    branch.line,
                    branch.col,
                )

    deps = _build_graph(step_table)
    order = _topological_order(step_table, deps)

    # guards[member] = [(branch_step, label), ...]
    guards = {name: [] for name in step_table}
    for step in steps:
        if step.branch:
            for case in step.branch.cases:
                for member in case.members:
                    guards[member].append((step.name, case.label))

    plan_params = {}
    for name, param in param_table.items():
        spec = {"type": param.ptype, "enum": list(param.enum_values) if param.enum_values else None}
        if param.has_default:
            spec["default"] = param.default
        plan_params[name] = spec

    plan_steps = {}
    for name, step in step_table.items():
        args = {}
        for key, value in step.args.items():
            args[key] = [value.kind, value.value]
        if step.branch:
            branch_plan = {"cond": step.branch.cond, "cases": {}}
            for case in step.branch.cases:
                branch_plan["cases"][case.label] = list(case.members)
        else:
            branch_plan = None
        plan_steps[name] = {
            "index": step.index,
            "line": step.line,
            "needs": [dep for dep, _, _ in step.needs],
            "args": args,
            "guards": [list(guard) for guard in guards[name]],
            "branch": branch_plan,
        }

    return {
        "params": plan_params,
        "steps": plan_steps,
        "stages": [[name] for name in order],
    }


def compile(text, optimize=False):
    """编译 DSL 文本；optimize=True 时返回经过优化的等价计划。"""
    params, steps = parse(text)
    plan = build_plan(params, steps)
    if optimize:
        from .optimizer import optimize_plan
        plan = optimize_plan(plan)
    return plan


def compile_file(path, optimize=False):
    with open(path, "r", encoding="utf-8") as handle:
        return compile(handle.read(), optimize=optimize)
