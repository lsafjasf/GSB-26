"""手工构造的边界结构用例 + 优化前后语句数 / 耗时统计。

运行：python3 cases.py
"""
import time

from ir import Instr as I, run, stmt_count
from optimizer import optimize, optimize_traced
from differential_test import results_equal
from trace import verify_trace


def case_loop_const():
    """循环内常量：循环体里的常量表达式应被折叠，不变量常量传播进循环。"""
    return [
        I('const', ('sum', 0)),
        I('const', ('i', 0)),
        I('const', ('one', 1)),
        I('const', ('ten', 10)),
        I('label', ('L',)),
        I('const', ('x', 5)),               # 循环内常量
        I('binop', ('y', '*', 'x', 'x')),   # 25，循环内可折叠
        I('binop', ('y', '+', 'y', 'one')), # 26，one 传播进循环后可折叠
        I('binop', ('sum', '+', 'sum', 'y')),
        I('binop', ('i', '+', 'i', 'one')),
        I('binop', ('t', '<', 'i', 'ten')),
        I('jnz', ('t', 'L')),
        I('print', ('sum',)),
        I('ret', ('sum',)),
    ], [(0, 0)]


def case_nested_branch():
    """嵌套分支：常量条件逐层裁剪，未走到的分支整体不可达。"""
    return [
        I('input', ('a', 0)),
        I('const', ('c1', 1)),
        I('const', ('c2', 1)),
        I('jnz', ('c1', 'L1')),
        I('print', ('a',)),          # 不可达
        I('binop', ('a', '+', 'a', 'c1')),  # 不可达
        I('label', ('L1',)),
        I('jz', ('c2', 'L2')),
        I('const', ('b', 40)),
        I('binop', ('b', '+', 'b', 'c1')),  # 折叠为 41
        I('print', ('b',)),
        I('jmp', ('L3',)),
        I('label', ('L2',)),
        I('print', ('a',)),          # 不可达
        I('label', ('L3',)),
        I('ret', ('b',)),
    ], [(7,), (-3,)]


def case_all_unreachable():
    """全部不可达：ret 之后的所有语句都应被删除。"""
    return [
        I('const', ('x', 1)),
        I('ret', ('x',)),
        I('const', ('y', 2)),
        I('print', ('y',)),
        I('binop', ('y', '+', 'y', 'y')),
        I('label', ('L',)),
        I('jmp', ('L',)),
        I('ret', ('y',)),
    ], [(0,)]


def case_self_jump():
    """跳转到自身：死循环必须保留，优化前后都超时且输出一致。"""
    return [
        I('const', ('x', 7)),
        I('print', ('x',)),
        I('label', ('L',)),
        I('jmp', ('L',)),
        I('ret', ('x',)),            # 不可达
    ], [(0,)]


def case_div_zero():
    """除零：编译期不得折叠 x//0，运行时行为（divzero）必须保持。"""
    return [
        I('const', ('a', 10)),
        I('const', ('z', 0)),
        I('binop', ('q', '//', 'a', 'z')),  # 不得折叠
        I('print', ('q',)),
        I('ret', ('q',)),
    ], [(0,)]


def case_div_zero_dead():
    """除零 + 死赋值：结果无人使用，但删除会去掉 divzero 行为，必须保留。"""
    return [
        I('const', ('a', 1)),
        I('const', ('z', 0)),
        I('binop', ('q', '%', 'a', 'z')),   # 死赋值但可能抛 divzero，保守保留
        I('const', ('r', 5)),
        I('ret', ('r',)),
    ], [(0,)]


def case_overflow():
    """溢出：64 位回绕语义下折叠与运行时逐位一致。"""
    big = (1 << 63) - 1
    return [
        I('const', ('m', big)),
        I('const', ('one', 1)),
        I('binop', ('x', '+', 'm', 'one')),   # 回绕到 -2^63
        I('binop', ('y', '*', 'x', 'x')),     # 回绕
        I('print', ('x',)),
        I('print', ('y',)),
        I('ret', ('y',)),
    ], [(0,)]


CASES = [
    ('loop_const', case_loop_const),
    ('nested_branch', case_nested_branch),
    ('all_unreachable', case_all_unreachable),
    ('self_jump', case_self_jump),
    ('div_zero', case_div_zero),
    ('div_zero_dead', case_div_zero_dead),
    ('overflow', case_overflow),
]


def check_case(name, make):
    prog, input_sets = make()
    t0 = time.perf_counter()
    opt, log, trace = optimize_traced(prog)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # 可追溯性核对：逐轮记录 / 来源映射 / 统计与实际改动一一对应
    verify_trace(prog, opt, trace)
    for inputs in input_sets:
        before = run(prog, inputs)
        after = run(opt, inputs)
        assert results_equal(before, after), (
            f"[{name}] 语义不一致 inputs={inputs}: {before} != {after}")
    # 不动点检查：再优化一次不应有任何改动
    opt2, log2 = optimize(opt)
    assert opt2 == opt and not log2, f"[{name}] 未达到不动点"
    return {
        'name': name,
        'before': stmt_count(prog),
        'after': stmt_count(opt),
        'removed': len(log),
        'ms': elapsed_ms,
        'log': log,
        'opt': opt,
    }


def main():
    print(f"{'case':<16} {'before':>6} {'after':>6} {'removed':>8} {'time_ms':>9}")
    print('-' * 50)
    results = [check_case(name, make) for name, make in CASES]
    for r in results:
        print(f"{r['name']:<16} {r['before']:>6} {r['after']:>6} "
              f"{r['removed']:>8} {r['ms']:>9.3f}")
    print()
    sample = next(r for r in results if r['name'] == 'nested_branch')
    print(f"删除记录样例（{sample['name']}）:")
    for d in sample['log']:
        print(f"  round={d.round} pos={d.position:<3} "
              f"orig={d.origin:<3} reason={d.reason:<22} instr={d.instr}")
    print()
    print(f"优化后程序（{sample['name']}）:")
    for ins in sample['opt']:
        print(f"  {ins}")
    print()
    print('ALL CASES OK')


if __name__ == '__main__':
    main()
