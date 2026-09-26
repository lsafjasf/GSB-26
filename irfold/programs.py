"""对拍用测试程序集：覆盖循环内常量、嵌套分支、全部不可达、跳转到自身、
除零、溢出等边界结构。"""

LOOP_CONST = """
# 循环体内的常量应被折叠；循环变量 i 不是常量，不得折叠
const i, 0
const n, 5
const c, 10
LOOP:
binop d, mul, c, 2
binop e, add, d, 1
print e
binop i, add, i, 1
binop cond, lt, i, n
cjmp cond, LOOP, END
END:
halt
"""

NESTED_BRANCH = """
# 嵌套分支：外层条件常量 1，内层条件常量 0，两侧死分支都应被裁掉
input x
const a, 1
cjmp a, T1, F1
F1:
print x
jmp END
T1:
const b, 0
cjmp b, T2, F2
T2:
print a
jmp END
F2:
const y, 42
binop y2, add, y, 8
print y2
jmp END
END:
halt
"""

ALL_UNREACHABLE = """
# jmp 之后与 halt 之后的代码全部不可达，应被整体删除
const x, 1
jmp SKIP
const y, 2
print y
binop z, add, y, 3
print z
SKIP:
print x
halt
const w, 9
print w
"""

SELF_JUMP = """
# 跳转到自身 = 死循环：必须保留，优化前后都应在步数上限处停下
const x, 5
print x
LOOP:
jmp LOOP
halt
"""

DIV_ZERO = """
# 除零：编译期不得折叠、不得触发异常，运行时陷阱行为必须保持
const a, 10
const b, 0
binop q, div, a, b
print q
halt
"""

DIV_ZERO_PRUNED = """
# 除零位于被裁剪的死分支中：删除后运行时不再陷阱，与原程序输出一致
const c, 1
cjmp c, OK, BAD
BAD:
const z, 0
binop w, div, c, z
print w
jmp END
OK:
print c
END:
halt
"""

OVERFLOW = """
# 64 位溢出：折叠与解释器使用同一环绕函数，结果必须一致
const big, 9223372036854775807
binop o, add, big, 1
print o
const m, 3037000500
binop p, mul, m, m
print p
const neg, -9223372036854775808
binop r, sub, neg, 1
print r
halt
"""

INPUT_PROP = """
# 输入不是常量：依赖输入的运算不得折叠，其余照常
input x
binop y, add, x, 1
print y
const k, 7
binop k2, mul, k, 6
print k2
cjmp x, A, B
A:
print k2
jmp E
B:
print y
E:
halt
"""

# (名字, 源码, 输入集合)
CASES = [
    ("loop_const", LOOP_CONST, [[]]),
    ("nested_branch", NESTED_BRANCH, [[0], [7], [-3]]),
    ("all_unreachable", ALL_UNREACHABLE, [[]]),
    ("self_jump", SELF_JUMP, [[]]),
    ("div_zero", DIV_ZERO, [[]]),
    ("div_zero_pruned", DIV_ZERO_PRUNED, [[]]),
    ("overflow", OVERFLOW, [[]]),
    ("input_prop", INPUT_PROP, [[0], [5], [-3], [2**63], [-2**63]]),
]
