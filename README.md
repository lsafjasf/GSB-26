# IR 常量折叠与死代码检测库

纯 Python 3 标准库实现：中间表示（IR）+ 解释器 + 常量传播/折叠 +
条件分支裁剪 + 不可达/死代码删除（迭代到不动点）+ 解释器对拍测试 +
全程可追溯（逐轮记录 / 来源映射 / 前后统计 + 重放式核对）。

## 文件

| 文件 | 说明 |
|---|---|
| `ir.py` | IR 指令集定义、64 位回绕机器语义、解释器 |
| `optimizer.py` | 优化器：`optimize(prog) -> (新程序, 删除记录)`；`optimize_traced(prog) -> (新程序, 删除记录, Trace)` |
| `trace.py` | 可追溯性：逐轮报告格式化 + `verify_trace` 重放式核对 |
| `cases.py` | 手工边界用例（循环内常量/嵌套分支/全部不可达/自跳转/除零/溢出）+ 统计 + trace 核对 |
| `differential_test.py` | 随机程序生成器 + 优化前后解释器对拍 + 每个程序的 trace 核对 |

## 运行命令

```bash
python3 cases.py                  # 边界用例 + 语句数/耗时统计 + 删除记录样例
python3 trace.py                  # 全部用例的逐轮优化记录 + 来源映射 + 前后统计 + 核对
python3 differential_test.py      # 对拍：默认 500 个随机程序 x 4 组输入
python3 differential_test.py 2000 42   # 指定程序数与随机种子
```

## 优化管线（每轮依次执行，直到一整轮无改动 = 不动点）

1. **常量传播**：CFG 上的抽象解释（worklist 算法），格为 `常量 < ⊤`；
2. **常量折叠 + 分支裁剪**：双操作数皆常量且可安全折叠时替换为 `const`；
   `jz/jnz` 条件为常量时改写为 `jmp` 或直接删除；
3. **不可达删除**：删去抽象状态为 ⊥ 的指令与无引用标号；
4. **死赋值删除**：反向活跃性分析，删除结果不再被使用的无副作用赋值。

每处删除记录 `Deletion(round, position, instr, reason)`，reason ∈
`unreachable` / `unreferenced-label` / `const-branch->jmp` /
`const-branch->removed` / `dead-assignment`，完整可追溯。

## 可追溯性（Trace）

`optimize_traced(prog)` 返回的 `Trace` 包含：

- **逐轮记录**（`trace.rounds`，仅含发生改动的轮次）：
  - `folded`：本轮折叠的常量 `FoldRecord`，含两个操作数的常量值，
    可独立重算 `eval_binop(op, lhs, rhs) == value`；
  - `branches`：本轮裁剪的条件分支 `BranchRecord`（`to-jmp` / `removed`，
    含条件寄存器的常量值与判定方向）；
  - `unreachable`：本轮判定为不可达的指令（报告中按位置连续段分组为块）；
  - `dead_labels` / `dead_assigns`：本轮删除的无引用标号与死赋值；
- **来源映射**（`trace.source_map`）：优化后位置 -> 原始位置，
  可还原任意保留指令的出处；被删除指令的出处见各删除记录的 `origin` 字段；
- **前后统计**（`trace.stats_before` / `trace.stats_after`）：
  指令数（不含 label）、标号数、分支数（jz/jnz）、跳转数（jmp）、
  循环数（回边数：跳转到不晚于自身位置的标号）。

`trace.verify_trace(original, optimized, trace)` 把 Trace 当重放脚本逐条核对：
每条原始指令要么被恰好一条删除记录覆盖、要么在来源映射中恰好出现一次；
删除记录中的指令形态与应用了此前重写记录后的实际形态一致；折叠值可重算；
保留指令与原始指令的任何差异都能被一条折叠/分支记录精确解释；
前后统计与重新计算一致。`cases.py` 与 `differential_test.py`
对每个用例 / 每个随机程序都会执行该核对。

## 除零与溢出的保守策略

- **溢出**：IR 语义本身定义为 64 位有符号回绕（`wrap64`），解释器与
  编译期折叠共用同一个 `eval_binop`，因此折叠结果与运行时逐位一致，
  编译期求值永不抛异常、永不改变行为（`INT64_MIN // -1` 等也按回绕定义）。
- **除零**：`x // 0` 与 `x % 0` 在运行时是**可观察行为**（结果为 `divzero`）。
  - 折叠：`foldable()` 对除数为常量 0 的运算拒绝折叠，原样保留指令；
  - 死代码删除：`//`、`%` 指令即使结果无人使用，也仅当常量传播证明
    除数为非零常量时才允许删除（见 `case_div_zero_dead`）。
- **超时/死循环**：解释器以步数上限识别死循环（如 `L: jmp L`），
  优化器从不删除可达代码，自旋环原样保留（见 `case_self_jump`）。
  对拍时两侧都 timeout 的情形按"输出流前缀一致"判定等价
  （优化会改变每条输出之间的指令数，但不改变输出流本身）。

## 实测数据

手工用例（`python3 cases.py`，语句数不含 label 伪指令）：

```
case             before  after  removed   time_ms
--------------------------------------------------
loop_const           13     11        2     0.246
nested_branch        13      6        9     0.146
all_unreachable       7      2        6     0.019
self_jump             4      3        1     0.024
div_zero              5      5        0     0.018
div_zero_dead         5      5        0     0.012
overflow              7      5        2     0.039
```

随机对拍（`python3 differential_test.py`，3 个种子各 500 程序 x 4 输入）：

```
种子 20260927: 不一致 0，语句 13140 -> 4956（删除 62.3%）
种子 1:        不一致 0，语句 12965 -> 4923（删除 62.0%）
种子 42:       不一致 0，语句 13088 -> 5263（删除 59.8%）
结果分布覆盖 ret / divzero / timeout 三种结局，且全部通过不动点检查
（对优化结果再优化一次无任何改动）。
```

## 删除记录样例（nested_branch，`python3 cases.py`）

```
round=0 pos=3   orig=3   reason=const-branch->jmp      instr=jnz c1 L1
round=0 pos=7   orig=7   reason=const-branch->removed  instr=jz c2 L2
round=0 pos=11  orig=12  reason=unreferenced-label     instr=label L2
round=0 pos=1   orig=1   reason=dead-assignment        instr=const c1 1
round=0 pos=2   orig=2   reason=dead-assignment        instr=const c2 1
round=0 pos=5   orig=5   reason=dead-assignment        instr=binop a + a c1
round=0 pos=7   orig=8   reason=dead-assignment        instr=const b 40
round=1 pos=2   orig=4   reason=unreachable            instr=print a
round=1 pos=7   orig=13  reason=unreachable            instr=print a
```

## 逐轮记录 / 来源映射 / 统计样例（nested_branch，`python3 trace.py`）

```
逐轮优化记录:
  round 0:
    折叠常量 1 条:
      [orig  9] binop b + b c1  =>  const b 41    (40 + 1 = 41)
    裁剪条件分支 2 条:
      [orig  3] jnz c1 L1  c1=1  => jmp L1（条件恒成立）
      [orig  7] jz c2 L2  c2=1  => 删除（条件恒不成立）
    删除无引用标号 1 条:
      [orig 12] label L2
    删除死赋值 4 条:
      [orig  1] const c1 1
      [orig  2] const c2 1
      [orig  5] binop a + a c1
      [orig  8] const b 40
  round 1:
    不可达块 2 个（共 2 条）:
      块 pos 2:
        [orig  4] print a
      块 pos 7:
        [orig 13] print a
来源映射:
  优化后位置 -> 原始位置（保留指令的出处）:
    [ 0] <- orig  0   input a 0
    [ 1] <- orig  3   jmp L1
    [ 2] <- orig  6   label L1
    [ 3] <- orig  9   const b 41
    [ 4] <- orig 10   print b
    [ 5] <- orig 11   jmp L3
    [ 6] <- orig 14   label L3
    [ 7] <- orig 15   ret b
统计:
  优化前: 指令 13 条 / 标号 3 个 / 分支 2 条 / 跳转 1 条 / 循环 0 个
  优化后: 指令 6 条 / 标号 2 个 / 分支 0 条 / 跳转 2 条 / 循环 0 个
  核对: verify_trace 通过，记录与实际改动一一对应
```

对应优化后程序：

```
input a 0
jmp L1
label L1
const b 41
print b
jmp L3
label L3
ret b
```
