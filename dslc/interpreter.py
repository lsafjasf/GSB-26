"""同一个解释器执行优化前/后的计划，用于语义对拍。

执行规则：
- 按 stages 顺序执行，同一阶段内按步骤名排序，得到确定性轨迹。
- 普通步骤：所有 needs 已执行则执行。
- 被分支 case 引用的步骤：还必须有某个守卫分支命中了包含它的 case 才执行。
- 分支步骤执行时按条件参数值选中 case（显式 case 优先，空 case 不回退兜底，
  未命中任何显式 case 时才使用 ``_``），激活对应成员。
"""

from .compiler import value_matches_type
from .errors import PlanInputError


def resolve_params(plan, inputs):
    inputs = inputs or {}
    env = {}
    for name, spec in plan["params"].items():
        if name in inputs:
            value = inputs[name]
            if not value_matches_type(spec["type"], spec["enum"], value):
                expected = spec["type"]
                if spec["type"] == "enum":
                    expected = f"enum({', '.join(spec['enum'])})"
                raise PlanInputError(
                    f"input ${name} expects {expected}, got {value!r}"
                )
            env[name] = value
        elif "default" in spec:
            env[name] = spec["default"]
        else:
            raise PlanInputError(f"missing required input parameter ${name}")
    return env


def execute(plan, inputs=None):
    """执行计划，返回步骤执行轨迹（步骤名列表）。"""
    env = resolve_params(plan, inputs)
    steps = plan["steps"]
    done = set()
    activated = set()
    trace = []

    for stage in plan["stages"]:
        for name in sorted(stage):
            step = steps[name]
            if any(dep not in done for dep in step["needs"]):
                continue
            guards = step["guards"]
            if guards and any(branch not in done for branch, _ in guards):
                continue
            if guards and name not in activated:
                continue
            trace.append(name)
            done.add(name)
            branch = step["branch"]
            if branch is not None:
                value = env[branch["cond"]]
                key = "true" if value is True else "false" if value is False else value
                cases = branch["cases"]
                if key in cases:
                    members = cases[key]
                else:
                    members = cases.get("_", [])
                activated.update(members)
    return trace


def trace_signature(plan, trace):
    """语义签名：执行步骤集合 + 已执行步骤之间所有因果有序对 (a 在 b 之前)。

    优化只允许重排无因果关系的步骤，因此两个计划轨迹的签名相同即语义等价。
    """
    steps = plan["steps"]
    executed = set(trace)
    successors = {name: set() for name in steps}
    for name, step in steps.items():
        for dep in step["needs"]:
            successors[dep].add(name)
        for branch, _label in step["guards"]:
            successors[branch].add(name)

    pairs = set()
    for source in executed:
        stack = [source]
        seen = set()
        while stack:
            current = stack.pop()
            for nxt in successors[current]:
                if nxt in executed and nxt not in seen:
                    seen.add(nxt)
                    pairs.add((source, nxt))
                    stack.append(nxt)
    return executed, pairs
