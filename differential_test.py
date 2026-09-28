"""解释器对拍测试：随机程序生成器 + 优化前后结果比对。

对同一批输入，分别解释执行优化前后的程序，要求结果元组完全一致
（包括正常返回值、输出序列、divzero、timeout 等可观察行为）。

运行：python3 differential_test.py [程序数] [种子]
"""
import random
import sys

from ir import Instr as I, run, stmt_count, BINOPS
from optimizer import optimize, optimize_traced
from trace import verify_trace

INPUT_SETS = [(0, 0), (1, 2), (-3, 7), (2**62, -5)]


def results_equal(before, after):
    """判断两个执行结果是否语义等价。

    优化不改变可观察事件（print）的顺序，但会改变每条输出之间
    执行的指令数。因此当两侧都因步数上限而 timeout 时，
    输出流是同一无限序列的不同长度前缀：较短者必须是较长者的前缀。
    其余情况（状态不同，或正常结束）要求完全相等。
    """
    if before[0] != after[0]:
        return False
    if before[0] == 'timeout':
        out1, out2 = before[1], after[1]
        short, long = (out1, out2) if len(out1) <= len(out2) else (out2, out1)
        return long[:len(short)] == short
    return before == after


def _short(result, limit=8):
    """截断超长输出序列，便于阅读。"""
    status, *rest = result
    parts = []
    for item in rest:
        if isinstance(item, tuple) and len(item) > limit:
            parts.append(item[:limit] + ('...',))
        else:
            parts.append(item)
    return (status, *parts)


def random_program(rng):
    """生成良定义的随机程序：寄存器先定义后使用，允许环与不可达代码。"""
    regs = ['r0', 'r1', 'r2', 'r3', 'in0', 'in1']
    prog = [I('const', (r, rng.randint(-5, 5))) for r in regs[:4]]
    prog += [I('input', ('in0', 0)), I('input', ('in1', 1))]

    n_labels = rng.randint(1, 4)
    labels = [f'L{k}' for k in range(n_labels)]
    body_len = rng.randint(6, 30)
    label_at = set(rng.sample(range(body_len), min(n_labels, body_len)))

    body = []
    for k in range(body_len):
        if k in label_at:
            body.append(I('label', (labels[len([b for b in body if b.op == 'label'])],)))
        roll = rng.random()
        dst = rng.choice(regs)
        if roll < 0.30:
            a, b = rng.choice(regs), rng.choice(regs)
            op = rng.choice(sorted(BINOPS))
            # 提高常量折叠命中率：有时直接用刚生成的常量寄存器
            body.append(I('binop', (dst, op, a, b)))
        elif roll < 0.45:
            body.append(I('const', (dst, rng.randint(-9, 9))))
        elif roll < 0.55:
            body.append(I('mov', (dst, rng.choice(regs))))
        elif roll < 0.65:
            body.append(I('print', (rng.choice(regs),)))
        elif roll < 0.80:
            # 条件跳转到随机标号（可能是前一个标号 -> 自旋环）
            cond = rng.choice(regs)
            op = rng.choice(['jz', 'jnz'])
            body.append(I(op, (cond, rng.choice(labels))))
        elif roll < 0.90:
            body.append(I('jmp', (rng.choice(labels),)))
        else:
            body.append(I('print', (rng.choice(regs),)))
    prog += body
    prog.append(I('ret', (rng.choice(regs),)))
    # 末尾之后追加一段必然不可达的代码，测试不可达删除
    if rng.random() < 0.5:
        prog.append(I('print', (rng.choice(regs),)))
        prog.append(I('binop', ('r0', '+', 'r0', 'r1')))
    return prog


def check_labels(prog):
    defined = {ins.args[0] for ins in prog if ins.op == 'label'}
    for ins in prog:
        if ins.op == 'jmp':
            assert ins.args[0] in defined, f"悬空跳转 {ins}"
        elif ins.op in ('jz', 'jnz'):
            assert ins.args[1] in defined, f"悬空跳转 {ins}"


def main():
    n_programs = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260927
    rng = random.Random(seed)

    outcomes = {}
    total_before = total_after = 0
    mismatches = 0
    for idx in range(n_programs):
        prog = random_program(rng)
        opt, log, trace = optimize_traced(prog)
        # 可追溯性核对：每个随机程序的逐轮记录 / 来源映射 / 统计
        # 都必须与实际改动一一对应
        verify_trace(prog, opt, trace)
        check_labels(opt)
        # 不动点：二次优化不得再变化
        opt2, log2 = optimize(opt)
        assert opt2 == opt and not log2, f"程序 {idx} 未达到不动点"
        total_before += stmt_count(prog)
        total_after += stmt_count(opt)
        for inputs in INPUT_SETS:
            before = run(prog, inputs)
            after = run(opt, inputs)
            outcomes[before[0]] = outcomes.get(before[0], 0) + 1
            if not results_equal(before, after):
                mismatches += 1
                print(f"MISMATCH 程序 {idx} inputs={inputs}")
                print(f"  before: {_short(before)}")
                print(f"  after:  {_short(after)}")
                print("  原程序:")
                for ins in prog:
                    print(f"    {ins}")
                print("  优化后:")
                for ins in opt:
                    print(f"    {ins}")
                if mismatches >= 3:
                    print("过多不一致，提前终止")
                    sys.exit(1)

    print(f"程序数:        {n_programs}")
    print(f"输入组/程序:   {len(INPUT_SETS)}")
    print(f"对拍执行次数:  {n_programs * len(INPUT_SETS)}")
    print(f"不一致数:      {mismatches}")
    print(f"语句总数:      {total_before} -> {total_after} "
          f"(删除 {total_before - total_after}, "
          f"{100 * (total_before - total_after) / max(total_before, 1):.1f}%)")
    print(f"结果分布:      {outcomes}")
    if mismatches:
        sys.exit(1)
    print("DIFFERENTIAL TEST OK")


if __name__ == '__main__':
    main()
