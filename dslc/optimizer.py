"""计划优化。

两种优化，均严格保持语义（执行的步骤集合与所有因果先后关系不变）：

1. 消除空分支
   - 空的 ``_`` 默认分支总是可删：命中与不命中都不激活任何步骤。
   - 非默认空 case 只有在没有非空 ``_`` 兜底时才可删。若存在非空 ``_``，
     空 case 的语义是"显式什么都不做"（不能回退到 ``_``），必须保留。
   - 若所有 case 都被消除，分支步骤退化为普通步骤：它本身仍执行（轨迹不变），
     且不激活任何东西。
2. 合并可并行的相邻步骤
   - 依赖（含分支到 case 成员的隐式依赖）构成 DAG。按"最长依赖路径"
     分层：level(s) = 0（无前驱）或 1 + max(level(前驱))。同一层内的步骤
     互相不可达，可以放进同一个并行阶段。
   - 该重排只交换没有因果关系的步骤，因此执行集合与 happens-before 偏序不变。
"""

import copy


def _dependencies(plan_steps):
    deps = {}
    for name, step in plan_steps.items():
        preds = set(step["needs"])
        for branch, _label in step["guards"]:
            preds.add(branch)
        deps[name] = preds
    return deps


def eliminate_empty_branches(plan):
    changed = False
    for step in plan["steps"].values():
        branch = step["branch"]
        if branch is None:
            continue
        cases = branch["cases"]
        has_live_default = bool(cases.get("_"))
        if not cases.get("_"):
            cases.pop("_", None)
        kept = {}
        for label, members in cases.items():
            if label == "_":
                kept[label] = members
            elif members or has_live_default:
                # 非空 case 保留；在存在非空兜底时，空 case 表达显式空动作，也保留。
                kept[label] = members
            else:
                changed = True
        if not kept:
            step["branch"] = None
            changed = True
        elif len(kept) != len(cases):
            branch["cases"] = kept
            changed = True
    return changed


def merge_parallel_stages(plan):
    steps = plan["steps"]
    deps = _dependencies(steps)
    order = [name for stage in plan["stages"] for name in stage]
    levels = {}
    for name in order:
        preds = deps[name]
        levels[name] = 0 if not preds else 1 + max(levels[p] for p in preds)

    grouped = {}
    for name in order:
        grouped.setdefault(levels[name], []).append(name)
    plan["stages"] = [grouped[level] for level in sorted(grouped)]
    return plan


def optimize_plan(plan):
    optimized = copy.deepcopy(plan)
    eliminate_empty_branches(optimized)
    merge_parallel_stages(optimized)
    return optimized
