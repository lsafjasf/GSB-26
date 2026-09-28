"""优化可追溯性：逐轮报告、来源映射、前后统计与可复跑验证。

verify_trace(original, optimized, trace) 把 Trace 当作"重放脚本"逐条核对：
    1) 每条原始指令要么被恰好一条删除记录覆盖，要么在来源映射中恰好
       出现一次（记录与实际改动一一对应，无遗漏、无重复）；
    2) 每条删除记录中的指令形态，必须与（应用了此前各轮折叠/分支重写
       记录之后的）实际形态一致；
    3) 每条折叠记录可独立重算：eval_binop(op, lhs, rhs) == value；
       每条分支记录的方向与条件常量值一致；
    4) 任意保留指令 optimized[j] 与原始指令 original[source_map[j]]
       的差异，必须能被一条折叠/分支重写记录精确解释
       （来源映射可还原任意保留指令的出处）；
    5) 前后统计与对原程序 / 优化结果重新计算的统计一致；
    6) 各阶段记录的位置下标单调且在当阶段程序长度内（位置可信）。

运行：python3 trace.py            # 全部边界用例的逐轮报告 + 验证
"""
from ir import Instr, eval_binop, foldable, run
from optimizer import optimize_traced, compute_stats


def _fail(msg):
    raise AssertionError(f"trace 核对失败: {msg}")


def _check_positions(rec, prog_len):
    """核对一轮内各阶段位置下标的单调性与边界，返回本轮结束时的程序长度。"""
    phase0 = ([d.position for d in rec.unreachable]
              + [b.position for b in rec.branches if b.action == 'removed'])
    size1 = prog_len - len(phase0)
    for seq, limit, what in (
            ([f.position for f in rec.folded], prog_len, 'fold'),
            ([b.position for b in rec.branches], prog_len, 'branch'),
            ([d.position for d in rec.unreachable], prog_len, 'unreachable'),
            ([d.position for d in rec.dead_labels], size1, 'dead-label'),
            ([d.position for d in rec.dead_assigns],
             size1 - len(rec.dead_labels), 'dead-assign')):
        if seq != sorted(seq):
            _fail(f"round {rec.round} {what} 位置非单调: {seq}")
        if seq and (seq[0] < 0 or seq[-1] >= limit):
            _fail(f"round {rec.round} {what} 位置越界: {seq} limit={limit}")
    return size1 - len(rec.dead_labels) - len(rec.dead_assigns)


def verify_trace(original, optimized, trace):
    """逐条核对 Trace 与实际改动一一对应。不通过则抛 AssertionError。"""
    n = len(original)
    current = list(original)   # origin -> 该指令重放后的当前形态
    deleted = {}               # origin -> 删除它的记录
    prog_len = n

    def mark_deleted(origin, record):
        if not (0 <= origin < n):
            _fail(f"origin 越界: {origin}")
        if origin in deleted:
            _fail(f"origin {origin} 被重复删除: {deleted[origin]} 与 {record}")
        deleted[origin] = record

    for rec in trace.rounds:
        prog_len = _check_positions(rec, prog_len)
        for f in rec.folded:
            if current[f.origin] != f.before:
                _fail(f"round {rec.round} 折叠前形态不符: "
                      f"记录 {f.before!r} != 实际 {current[f.origin]!r}")
            if f.before.op != 'binop':
                _fail(f"round {rec.round} 折叠对象不是 binop: {f.before!r}")
            if not foldable(f.op, f.lhs, f.rhs):
                _fail(f"round {rec.round} 折叠了不可折叠的运算: {f}")
            if eval_binop(f.op, f.lhs, f.rhs) != f.value:
                _fail(f"round {rec.round} 折叠值错误: {f}")
            if f.after != Instr('const', (f.before.args[0], f.value)):
                _fail(f"round {rec.round} 折叠结果指令错误: {f.after!r}")
            current[f.origin] = f.after
        for b in rec.branches:
            if current[b.origin] != b.before:
                _fail(f"round {rec.round} 分支裁剪前形态不符: "
                      f"记录 {b.before!r} != 实际 {current[b.origin]!r}")
            op, (cond, label) = b.before.op, b.before.args
            if op not in ('jz', 'jnz') or cond != b.cond:
                _fail(f"round {rec.round} 分支记录内容错误: {b}")
            taken = (b.cond_value == 0) if op == 'jz' else (b.cond_value != 0)
            if b.action == 'to-jmp':
                if not taken or b.after != Instr('jmp', (label,)):
                    _fail(f"round {rec.round} 分支方向与改写不符: {b}")
                current[b.origin] = b.after
            elif b.action == 'removed':
                if taken or b.after is not None:
                    _fail(f"round {rec.round} 分支方向与删除不符: {b}")
                mark_deleted(b.origin, b)
            else:
                _fail(f"round {rec.round} 未知分支动作: {b.action}")
        for group in (rec.unreachable, rec.dead_labels, rec.dead_assigns):
            for d in group:
                if repr(current[d.origin]) != d.instr:
                    _fail(f"round {rec.round} 删除形态不符: "
                          f"记录 {d.instr} != 实际 {current[d.origin]!r}")
                mark_deleted(d.origin, d)

    if prog_len != len(optimized):
        _fail(f"重放后程序长度 {prog_len} != 优化结果长度 {len(optimized)}")

    # 覆盖性：每条原始指令恰好被删除一次或保留一次
    sm = trace.source_map
    if len(sm) != len(optimized):
        _fail(f"source_map 长度 {len(sm)} != 优化结果长度 {len(optimized)}")
    if list(sm) != sorted(sm) or len(set(sm)) != len(sm):
        _fail(f"source_map 非严格递增: {sm}")
    retained = [o for o in range(n) if o not in deleted]
    if list(sm) != retained:
        _fail(f"删除集与保留集不互补: 保留 {retained} != source_map {sm}")

    # 出处还原：保留指令与原始指令的差异必须被重写记录精确解释
    for j, o in enumerate(sm):
        if optimized[j] != current[o]:
            _fail(f"optimized[{j}]={optimized[j]!r} 与 origin {o} 重放形态 "
                  f"{current[o]!r} 不一致")

    # 统计一致
    if trace.stats_before != compute_stats(original):
        _fail(f"stats_before 不符: {trace.stats_before} != "
              f"{compute_stats(original)}")
    if trace.stats_after != compute_stats(optimized):
        _fail(f"stats_after 不符: {trace.stats_after} != "
              f"{compute_stats(optimized)}")
    return True


def group_unreachable(rec):
    """把一轮的不可达删除按位置连续段分组为"块"。"""
    blocks = []
    for d in rec.unreachable:
        if blocks and d.position == blocks[-1][-1].position + 1:
            blocks[-1].append(d)
        else:
            blocks.append([d])
    return blocks


def format_stats(stats):
    return (f"指令 {stats.stmts} 条 / 标号 {stats.labels} 个 / "
            f"分支 {stats.branches} 条 / 跳转 {stats.jumps} 条 / "
            f"循环 {stats.loops} 个")


def format_trace(trace):
    """把 Trace 格式化为可逐条核对的文本报告。"""
    lines = []
    for rec in trace.rounds:
        lines.append(f"  round {rec.round}:")
        if rec.folded:
            lines.append(f"    折叠常量 {len(rec.folded)} 条:")
            for f in rec.folded:
                lines.append(f"      [orig {f.origin:>2}] {f.before}  =>  "
                             f"{f.after}    ({f.lhs} {f.op} {f.rhs}"
                             f" = {f.value})")
        if rec.branches:
            lines.append(f"    裁剪条件分支 {len(rec.branches)} 条:")
            for b in rec.branches:
                if b.action == 'to-jmp':
                    desc = f"=> {b.after}（条件恒成立）"
                else:
                    desc = "=> 删除（条件恒不成立）"
                lines.append(f"      [orig {b.origin:>2}] {b.before}  "
                             f"{b.cond}={b.cond_value}  {desc}")
        if rec.unreachable:
            blocks = group_unreachable(rec)
            total = len(rec.unreachable)
            lines.append(f"    不可达块 {len(blocks)} 个（共 {total} 条）:")
            for blk in blocks:
                rng = (f"pos {blk[0].position}"
                       if len(blk) == 1 else
                       f"pos {blk[0].position}..{blk[-1].position}")
                lines.append(f"      块 {rng}:")
                for d in blk:
                    lines.append(f"        [orig {d.origin:>2}] {d.instr}")
        if rec.dead_labels:
            lines.append(f"    删除无引用标号 {len(rec.dead_labels)} 条:")
            for d in rec.dead_labels:
                lines.append(f"      [orig {d.origin:>2}] {d.instr}")
        if rec.dead_assigns:
            lines.append(f"    删除死赋值 {len(rec.dead_assigns)} 条:")
            for d in rec.dead_assigns:
                lines.append(f"      [orig {d.origin:>2}] {d.instr}")
    if not trace.rounds:
        lines.append("  （无任何改动，原程序即为不动点）")
    return "\n".join(lines)


def format_source_map(trace, optimized):
    lines = ["  优化后位置 -> 原始位置（保留指令的出处）:"]
    for j, o in enumerate(trace.source_map):
        lines.append(f"    [{j:>2}] <- orig {o:>2}   {optimized[j]}")
    return "\n".join(lines)


def main():
    from cases import CASES
    from differential_test import results_equal

    for name, make in CASES:
        prog, input_sets = make()
        opt, log, trace = optimize_traced(prog)
        verify_trace(prog, opt, trace)
        for inputs in input_sets:
            before, after = run(prog, inputs), run(opt, inputs)
            assert results_equal(before, after), (
                f"[{name}] 语义不一致 inputs={inputs}: {before} != {after}")
        print(f"=== {name} " + "=" * (60 - len(name)))
        print("逐轮优化记录:")
        print(format_trace(trace))
        print("来源映射:")
        print(format_source_map(trace, opt))
        print("统计:")
        print(f"  优化前: {format_stats(trace.stats_before)}")
        print(f"  优化后: {format_stats(trace.stats_after)}")
        print("  核对: verify_trace 通过，记录与实际改动一一对应")
        print()
    print("TRACE VERIFY OK (全部用例：逐轮记录 / 来源映射 / 统计 均核对通过)")


if __name__ == '__main__':
    main()
