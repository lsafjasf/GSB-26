"""常量折叠 / 死代码删除优化器。

流水线（迭代到不动点）：
1. ``fold_pass``        常量传播 + 常量折叠 + 条件常量分支裁剪（cjmp -> jmp）
2. ``unreachable_pass`` 不可达代码识别与删除（从入口沿 CFG 可达性分析）
3. ``dce_pass``         死赋值删除（反向活跃变量分析）
4. ``simplify_jumps``   删除跳转到下一条指令的冗余 jmp

保守策略（保证语义等价）：
- 折叠复用解释器的 ``eval_binop``，溢出环绕语义与运行时完全一致；
- 编译期求值若触发 Trap（除零），**放弃折叠**，把异常留给运行时原样抛出；
- 可能陷阱的 ``div``/``mod`` 指令永不作为死代码删除；
- 分支裁剪只在条件被证明为常量时进行，被裁掉的分支运行时必然不执行。
"""
from __future__ import annotations

from collections import deque

from .interp import Trap, eval_binop, wrap
from .ir import Instr, Program, defs, is_int, successors, uses

NAC = object()  # not-a-constant


# ---------------------------------------------------------------- 常量传播

def _resolve(env, x):
    if is_int(x):
        return int(x)
    return env.get(x, NAC)


def _transfer(ins, env):
    out = dict(env)
    op = ins.op
    if op == "const":
        out[ins.args[0]] = wrap(int(ins.args[1]))
    elif op == "input":
        out.pop(ins.args[0], None)
    elif op == "mov":
        v = _resolve(env, ins.args[1])
        if v is NAC:
            out.pop(ins.args[0], None)
        else:
            out[ins.args[0]] = v
    elif op == "binop":
        a = _resolve(env, ins.args[2])
        b = _resolve(env, ins.args[3])
        v = NAC
        if a is not NAC and b is not NAC:
            try:
                v = eval_binop(ins.args[1], a, b)
            except Trap:
                v = NAC  # 保守：运行时会陷阱，不传播任何值
        if v is NAC:
            out.pop(ins.args[0], None)
        else:
            out[ins.args[0]] = v
    return out


def _meet(a, b):
    """汇合：变量只有在两条路径上是同一个常量时才是常量。"""
    result = {}
    for k in set(a) | set(b):
        if k in a and k in b and a[k] == b[k]:
            result[k] = a[k]
    return result


def const_prop_envs(prog):
    """返回每条指令入口处的常量环境（worklist 数据流分析）。"""
    n = len(prog.instrs)
    succs = successors(prog)
    in_env = [None] * n
    if n == 0:
        return []
    in_env[0] = {}
    worklist = deque([0])
    while worklist:
        i = worklist.popleft()
        out = _transfer(prog.instrs[i], in_env[i])
        for s in succs[i]:
            if in_env[s] is None:
                in_env[s] = out
                worklist.append(s)
            else:
                merged = _meet(in_env[s], out)
                if merged != in_env[s]:
                    in_env[s] = merged
                    worklist.append(s)
    return [e if e is not None else {} for e in in_env]



# ---------------------------------------------------------------- 指令删除

def _remove_instrs(prog, remove_set):
    """删除指定下标的指令，并修复标签：

    - 被删指令的标签移动到下一个保留的指令上；
    - 若该指令已有标签，则记录别名并重写所有跳转；
    - 若被删的是程序末尾，用 ``halt`` 承载标签（语义等同于掉到末尾）。
    """
    n = len(prog.instrs)
    nxt = [None] * n
    nxt_kept = None
    for i in range(n - 1, -1, -1):
        if i not in remove_set:
            nxt_kept = i
        nxt[i] = nxt_kept
    alias = {}
    moved = {}
    tail_labels = []
    for i in sorted(remove_set):
        lbl = prog.instrs[i].label
        if lbl is None:
            continue
        j = nxt[i]
        if j is None:
            tail_labels.append(lbl)
        else:
            tgt = prog.instrs[j].label
            if tgt is None and j not in moved:
                moved[j] = lbl
            else:
                alias[lbl] = tgt if tgt is not None else moved[j]
    new = []
    for i, ins in enumerate(prog.instrs):
        if i in remove_set:
            continue
        label = ins.label if ins.label is not None else moved.get(i)
        new.append(Instr(ins.op, list(ins.args), label))
    for lbl in tail_labels:
        new.append(Instr("halt", [], lbl))
    for ins in new:
        if ins.op == "jmp":
            ins.args[0] = alias.get(ins.args[0], ins.args[0])
        elif ins.op == "cjmp":
            ins.args[1] = alias.get(ins.args[1], ins.args[1])
            ins.args[2] = alias.get(ins.args[2], ins.args[2])
    return Program(new)


# ---------------------------------------------------------------- 各优化遍

def _location(i, ins):
    return f"{ins.label} (index {i})" if ins.label else f"index {i}"


def fold_pass(prog, log, iteration):
    """常量传播 + 折叠 + 条件常量分支裁剪。返回 (新程序, 是否有变化)。"""
    envs = const_prop_envs(prog)
    new = []
    changed = False
    for i, ins in enumerate(prog.instrs):
        env = envs[i]
        op = ins.op
        if op == "binop":
            dst, bop, a, b = ins.args
            av, bv = _resolve(env, a), _resolve(env, b)
            if av is not NAC and bv is not NAC:
                try:
                    res = eval_binop(bop, av, bv)
                except Trap:
                    # 保守策略：除零不在编译期触发，保留指令让运行时原样陷阱
                    new.append(ins)
                    continue
                new.append(Instr("const", [dst, str(res)], ins.label))
                log.append({
                    "iteration": iteration, "pass": "const_fold", "action": "fold",
                    "location": _location(i, ins), "instruction": ins.text(),
                    "reason": f"binop {bop} on constants {av}, {bv} -> {res}",
                })
                changed = True
                continue
            a2 = str(av) if av is not NAC and not is_int(a) else a
            b2 = str(bv) if bv is not NAC and not is_int(b) else b
            if (a2, b2) != (a, b):
                changed = True
            new.append(Instr("binop", [dst, bop, a2, b2], ins.label))
        elif op == "mov":
            dst, src = ins.args
            v = _resolve(env, src)
            if v is not NAC:
                new.append(Instr("const", [dst, str(v)], ins.label))
                log.append({
                    "iteration": iteration, "pass": "const_fold", "action": "fold",
                    "location": _location(i, ins), "instruction": ins.text(),
                    "reason": f"mov of constant {v} -> const",
                })
                changed = True
            else:
                new.append(ins)
        elif op == "cjmp":
            cond, l1, l2 = ins.args
            cv = _resolve(env, cond)
            if cv is not NAC:
                target, dead = (l1, l2) if cv != 0 else (l2, l1)
                new.append(Instr("jmp", [target], ins.label))
                log.append({
                    "iteration": iteration, "pass": "branch_prune", "action": "prune",
                    "location": _location(i, ins), "instruction": ins.text(),
                    "reason": f"condition is constant {cv}; branch to {dead} is dead",
                })
                changed = True
            else:
                new.append(ins)
        elif op == "print":
            src = ins.args[0]
            v = _resolve(env, src)
            if v is not NAC and not is_int(src):
                new.append(Instr("print", [str(v)], ins.label))
                changed = True
            else:
                new.append(ins)
        else:
            new.append(ins)
    return Program(new), changed


def unreachable_pass(prog, log, iteration):
    """删除从入口不可达的指令。"""
    n = len(prog.instrs)
    if n == 0:
        return prog, False
    succs = successors(prog)
    seen = {0}
    stack = [0]
    while stack:
        i = stack.pop()
        for s in succs[i]:
            if s not in seen:
                seen.add(s)
                stack.append(s)
    remove_set = set()
    for i, ins in enumerate(prog.instrs):
        if i not in seen:
            log.append({
                "iteration": iteration, "pass": "unreachable", "action": "remove",
                "location": _location(i, ins), "instruction": ins.text(),
                "reason": "not reachable from program entry",
            })
            remove_set.add(i)
    if not remove_set:
        return prog, False
    return _remove_instrs(prog, remove_set), True


def dce_pass(prog, log, iteration):
    """死赋值删除（反向活跃变量分析）。div/mod 可能陷阱，永不删除。"""
    n = len(prog.instrs)
    if n == 0:
        return prog, False
    succs = successors(prog)
    live_in = [set() for _ in range(n)]
    live_out = [set() for _ in range(n)]
    changed_flag = True
    while changed_flag:
        changed_flag = False
        for i in range(n - 1, -1, -1):
            ins = prog.instrs[i]
            out = set()
            for s in succs[i]:
                out |= live_in[s]
            in_new = (out - set(defs(ins))) | set(uses(ins))
            if out != live_out[i] or in_new != live_in[i]:
                live_out[i] = out
                live_in[i] = in_new
                changed_flag = True
    remove_set = set()
    for i, ins in enumerate(prog.instrs):
        removable = ins.op in ("const", "mov") or (
            ins.op == "binop" and ins.args[1] not in ("div", "mod")
        )
        if removable and ins.args[0] not in live_out[i]:
            log.append({
                "iteration": iteration, "pass": "dead_code", "action": "remove",
                "location": _location(i, ins), "instruction": ins.text(),
                "reason": f"assigned variable '{ins.args[0]}' is not live afterwards",
            })
            remove_set.add(i)
    if not remove_set:
        return prog, False
    return _remove_instrs(prog, remove_set), True


def simplify_jumps_pass(prog, log, iteration):
    """删除目标就是下一条指令的冗余 jmp。"""
    labels = prog.label_map()
    remove_set = set()
    for i, ins in enumerate(prog.instrs):
        if ins.op == "jmp" and labels[ins.args[0]] == i + 1:
            log.append({
                "iteration": iteration, "pass": "simplify_jumps", "action": "remove",
                "location": _location(i, ins), "instruction": ins.text(),
                "reason": "jump target is the immediately following instruction",
            })
            remove_set.add(i)
    if not remove_set:
        return prog, False
    return _remove_instrs(prog, remove_set), True


def clean_labels_pass(prog, log, iteration):
    """删除不再被任何跳转引用的标签（纯外观清理，不改变语义）。"""
    referenced = set()
    for ins in prog.instrs:
        if ins.op == "jmp":
            referenced.add(ins.args[0])
        elif ins.op == "cjmp":
            referenced.add(ins.args[1])
            referenced.add(ins.args[2])
    new = []
    changed = False
    for i, ins in enumerate(prog.instrs):
        if ins.label is not None and ins.label not in referenced:
            log.append({
                "iteration": iteration, "pass": "clean_labels", "action": "remove",
                "location": _location(i, ins), "instruction": ins.text(),
                "reason": f"label '{ins.label}' is no longer referenced by any jump",
            })
            new.append(Instr(ins.op, ins.args, None))
            changed = True
        else:
            new.append(ins)
    return Program(new), changed


# ---------------------------------------------------------------- 不动点驱动

def optimize(prog, max_iter=100):
    """迭代执行各优化遍直到不动点。返回 (优化后程序, 删除/折叠日志, 迭代轮数)。"""
    log = []
    cur = prog
    iterations = 0
    for it in range(1, max_iter + 1):
        iterations = it
        before = cur.dump()
        for p in (fold_pass, unreachable_pass, dce_pass,
                  simplify_jumps_pass, clean_labels_pass):
            cur, _ = p(cur, log, it)
        if cur.dump() == before:
            break
    return cur, log, iterations
