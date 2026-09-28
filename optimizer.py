"""常量折叠 / 传播、条件分支裁剪、不可达与死代码删除（迭代到不动点）。

优化管线（每轮依次执行，直到一整轮没有任何改动为止）：
    1. 常量传播（CFG 上的抽象解释，worklist 不动点算法）
    2. 常量折叠 + 常量条件分支裁剪
    3. 不可达指令与无引用标号删除
    4. 死赋值删除（基于活跃性分析）

可追溯性（本次迭代新增）：
    * 每处删除记录 Deletion(round, position, instr, reason, origin)，
      origin 为该指令在原始程序中的位置（跨轮稳定，是核对的主键）；
    * optimize_traced() 额外返回 Trace：
        - rounds：逐轮的常量折叠 / 分支裁剪 / 不可达 / 死代码记录；
        - source_map：来源映射，优化后位置 -> 原始位置，
          可还原任意保留指令的出处；
        - stats_before / stats_after：优化前后统计
          （指令数、分支数、循环数等）；
    * trace.py 的 verify_trace() 把记录当重放脚本逐条核对，
      保证记录与实际改动一一对应。
"""
from collections import namedtuple

from ir import Instr, BINOPS, DIV_OPS, eval_binop, foldable, wrap64

# origin：指令在原始程序中的位置；position：记录时该阶段所见程序内的下标。
Deletion = namedtuple(
    'Deletion', ['round', 'position', 'instr', 'reason', 'origin'],
    defaults=[None])

# 一次常量折叠：binop -> const。lhs/rhs 为折叠时两个操作数的常量值，
# 可独立核对 eval_binop(op, lhs, rhs) == value。
FoldRecord = namedtuple(
    'FoldRecord',
    ['round', 'position', 'origin', 'before', 'after', 'op', 'lhs', 'rhs',
     'value'])

# 一次常量条件分支裁剪：action 为 'to-jmp'（改写为 jmp）或 'removed'（删除）。
BranchRecord = namedtuple(
    'BranchRecord',
    ['round', 'position', 'origin', 'before', 'action', 'cond', 'cond_value',
     'after'])

# 程序统计：stmts 指令数（不含 label 伪指令）、labels 标号数、
# branches 条件分支数（jz/jnz）、jumps 无条件跳转数、loops 循环数（回边数）。
Stats = namedtuple(
    'Stats', ['stmts', 'labels', 'branches', 'jumps', 'loops'])


class RoundRecord:
    """一轮优化的完整记录。"""

    def __init__(self, round_no):
        self.round = round_no
        self.folded = []         # FoldRecord：本轮折叠的常量
        self.branches = []       # BranchRecord：本轮裁剪的条件分支
        self.unreachable = []    # Deletion：本轮判定为不可达的指令
        self.dead_labels = []    # Deletion：本轮删除的无引用标号
        self.dead_assigns = []   # Deletion：本轮删除的死赋值

    @property
    def changed(self):
        return any((self.folded, self.branches, self.unreachable,
                    self.dead_labels, self.dead_assigns))


class Trace:
    """一次优化的完整轨迹：逐轮记录 + 来源映射 + 前后统计。"""

    def __init__(self):
        self.rounds = []        # RoundRecord，仅含发生改动的轮次
        self.source_map = []    # 优化后位置 -> 原始位置
        self.stats_before = None
        self.stats_after = None


# 抽象值：('c', v) 常量；('t',) 未知（top）；缺失键 = 底部（尚未赋值）
_CONST = 'c'
_TOP = ('t',)


def _labels_of(prog):
    return {ins.args[0]: i for i, ins in enumerate(prog) if ins.op == 'label'}


def _successors(prog, i, labels):
    ins = prog[i]
    op = ins.op
    nxt = i + 1 if i + 1 < len(prog) else None
    if op == 'jmp':
        return [labels[ins.args[0]]]
    if op in ('jz', 'jnz'):
        succ = [labels[ins.args[1]]]
        if nxt is not None:
            succ.append(nxt)
        return succ
    if op in ('ret', 'halt'):
        return []
    return [nxt] if nxt is not None else []


def count_loops(prog):
    """循环数 = 回边数：跳转到不晚于自身位置的标号的 jmp/jz/jnz 条数。"""
    labels = _labels_of(prog)
    n = 0
    for i, ins in enumerate(prog):
        if ins.op in ('jmp', 'jz', 'jnz') and labels[ins.args[-1]] <= i:
            n += 1
    return n


def compute_stats(prog):
    """统计指令数 / 标号数 / 分支数 / 跳转数 / 循环数。"""
    stmts = labels = branches = jumps = 0
    for ins in prog:
        if ins.op == 'label':
            labels += 1
        else:
            stmts += 1
            if ins.op in ('jz', 'jnz'):
                branches += 1
            elif ins.op == 'jmp':
                jumps += 1
    return Stats(stmts, labels, branches, jumps, count_loops(prog))


def _join_val(x, y):
    if x is None:
        return y
    if y is None:
        return x
    if x == y:
        return x
    return _TOP


def _join(s1, s2):
    keys = set(s1) | set(s2)
    return {k: _join_val(s1.get(k), s2.get(k)) for k in keys}


def _transfer(ins, st):
    st = dict(st)
    op = ins.op
    if op == 'const':
        st[ins.args[0]] = (_CONST, wrap64(ins.args[1]))
    elif op == 'input':
        st[ins.args[0]] = _TOP
    elif op == 'mov':
        st[ins.args[0]] = st.get(ins.args[1], _TOP)
    elif op == 'binop':
        dst, bop, a, b = ins.args
        va, vb = st.get(a), st.get(b)
        if (va and vb and va[0] == _CONST and vb[0] == _CONST
                and foldable(bop, va[1], vb[1])):
            st[dst] = (_CONST, eval_binop(bop, va[1], vb[1]))
        else:
            st[dst] = _TOP
    return st


def const_prop(prog):
    """常量传播分析，返回每条指令的入口抽象状态（None 表示不可达）。"""
    n = len(prog)
    if n == 0:
        return []
    labels = _labels_of(prog)
    in_state = [None] * n
    in_state[0] = {}
    worklist = [0]
    while worklist:
        i = worklist.pop()
        st = in_state[i]
        if st is None:
            continue
        out = _transfer(prog[i], st)
        for j in _successors(prog, i, labels):
            if in_state[j] is None:
                in_state[j] = out
                worklist.append(j)
            else:
                merged = _join(in_state[j], out)
                if merged != in_state[j]:
                    in_state[j] = merged
                    worklist.append(j)
    return in_state


def _uses(ins):
    op = ins.op
    if op == 'mov':
        return {ins.args[1]}
    if op == 'binop':
        return {ins.args[2], ins.args[3]}
    if op in ('print', 'ret'):
        return {ins.args[0]}
    if op in ('jz', 'jnz'):
        return {ins.args[0]}
    return set()


def _defs(ins):
    if ins.op in ('const', 'input', 'mov', 'binop'):
        return {ins.args[0]}
    return set()


def _rewrite(prog, origin, in_state, log, rec, round_no):
    """常量折叠 + 分支裁剪 + 不可达删除。返回 (新程序, 新origin, 是否有改动)。"""
    new = []
    new_origin = []
    changed = False
    for i, ins in enumerate(prog):
        st = in_state[i]
        if st is None:
            d = Deletion(round_no, i, repr(ins), 'unreachable', origin[i])
            log.append(d)
            rec.unreachable.append(d)
            changed = True
            continue
        op = ins.op
        if op == 'binop':
            dst, bop, a, b = ins.args
            va, vb = st.get(a), st.get(b)
            if va and vb and va[0] == _CONST and vb[0] == _CONST:
                if foldable(bop, va[1], vb[1]):
                    value = eval_binop(bop, va[1], vb[1])
                    folded = Instr('const', (dst, value))
                    rec.folded.append(FoldRecord(
                        round_no, i, origin[i], ins, folded,
                        bop, va[1], vb[1], value))
                    new.append(folded)
                    new_origin.append(origin[i])
                    changed = True
                    continue
                # 除零：保守保留，运行时必须产生相同的 divzero 行为
        elif op in ('jz', 'jnz'):
            cond, label = ins.args
            vc = st.get(cond)
            if vc and vc[0] == _CONST:
                taken = (vc[1] == 0) if op == 'jz' else (vc[1] != 0)
                if taken:
                    new_ins = Instr('jmp', (label,))
                    new.append(new_ins)
                    new_origin.append(origin[i])
                    d = Deletion(round_no, i, repr(ins),
                                 'const-branch->jmp', origin[i])
                    log.append(d)
                    rec.branches.append(BranchRecord(
                        round_no, i, origin[i], ins, 'to-jmp',
                        cond, vc[1], new_ins))
                else:
                    d = Deletion(round_no, i, repr(ins),
                                 'const-branch->removed', origin[i])
                    log.append(d)
                    rec.branches.append(BranchRecord(
                        round_no, i, origin[i], ins, 'removed',
                        cond, vc[1], None))
                changed = True
                continue
        new.append(ins)
        new_origin.append(origin[i])
    return new, new_origin, changed


def _remove_dead_labels(prog, origin, log, rec, round_no):
    referenced = set()
    for ins in prog:
        if ins.op == 'jmp':
            referenced.add(ins.args[0])
        elif ins.op in ('jz', 'jnz'):
            referenced.add(ins.args[1])
    new = []
    new_origin = []
    changed = False
    for i, ins in enumerate(prog):
        if ins.op == 'label' and ins.args[0] not in referenced:
            d = Deletion(round_no, i, repr(ins), 'unreferenced-label',
                         origin[i])
            log.append(d)
            rec.dead_labels.append(d)
            changed = True
            continue
        new.append(ins)
        new_origin.append(origin[i])
    return new, new_origin, changed


def _dead_elim(prog, origin, in_state, log, rec, round_no):
    """死赋值删除（活跃性分析）。

    保守点：// 与 % 在除数可能为零时会抛出可观察的 divzero，
    仅当除数被常量传播证明为非零常量时才允许删除。
    """
    n = len(prog)
    labels = _labels_of(prog)
    live_out = [set() for _ in range(n)]
    changed_flag = True
    while changed_flag:
        changed_flag = False
        for i in range(n - 1, -1, -1):
            ins = prog[i]
            new_out = set()
            for j in _successors(prog, i, labels):
                new_out |= _uses(prog[j])
                new_out |= (live_out[j] - _defs(prog[j]))
            if new_out != live_out[i]:
                live_out[i] = new_out
                changed_flag = True
    new = []
    new_origin = []
    changed = False
    for i, ins in enumerate(prog):
        d = _defs(ins)
        if d and ins.op in ('const', 'mov', 'binop') and not (d & live_out[i]):
            if ins.op == 'binop' and ins.args[1] in DIV_OPS:
                vb = in_state[i].get(ins.args[3]) if in_state[i] else None
                divisor_nonzero = (vb and vb[0] == _CONST and vb[1] != 0)
                if not divisor_nonzero:
                    new.append(ins)  # 可能抛 divzero，保守保留
                    new_origin.append(origin[i])
                    continue
            rec_d = Deletion(round_no, i, repr(ins), 'dead-assignment',
                             origin[i])
            log.append(rec_d)
            rec.dead_assigns.append(rec_d)
            changed = True
            continue
        new.append(ins)
        new_origin.append(origin[i])
    return new, new_origin, changed


def _one_pass(prog, origin, log, rec, round_no):
    in_state = const_prop(prog)
    prog, origin, c1 = _rewrite(prog, origin, in_state, log, rec, round_no)
    prog, origin, c2 = _remove_dead_labels(prog, origin, log, rec, round_no)
    in_state2 = const_prop(prog) if (c1 or c2) else in_state
    prog, origin, c3 = _dead_elim(prog, origin, in_state2, log, rec, round_no)
    return prog, origin, (c1 or c2 or c3)


def optimize_traced(prog, max_rounds=1000):
    """优化程序到不动点。返回 (新程序, 删除记录列表, Trace)。"""
    prog = list(prog)
    origin = list(range(len(prog)))
    log = []
    trace = Trace()
    trace.stats_before = compute_stats(prog)
    for round_no in range(max_rounds):
        rec = RoundRecord(round_no)
        prog, origin, changed = _one_pass(prog, origin, log, rec, round_no)
        if not changed:
            break
        trace.rounds.append(rec)
    trace.source_map = origin
    trace.stats_after = compute_stats(prog)
    return prog, log, trace


def optimize(prog, max_rounds=1000):
    """优化程序到不动点。返回 (新程序, 删除记录列表)。"""
    opt, log, _trace = optimize_traced(prog, max_rounds)
    return opt, log
