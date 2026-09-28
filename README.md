# irfold — 中间表示的常量折叠与死代码检测库

纯 Python 3 标准库实现，无第三方依赖。

## 功能

- **常量传播与折叠**：worklist 数据流分析（汇合 = 同一常量才保留），`binop`/`mov` 折叠为 `const`，变量操作数替换为字面量。
- **条件常量分支裁剪**：`cjmp` 条件被证明为常量时改写为 `jmp`，死分支随后被不可达分析删除。
- **不可达代码识别与删除**：从入口沿 CFG 做可达性分析，删除不可达指令。
- **死赋值删除**：反向活跃变量分析，删除不再被使用的 `const`/`mov`/`binop`。
- **迭代到不动点**：各优化遍循环执行直到程序文本不再变化。
- **可追溯的删除记录**：每次折叠/裁剪/删除都记录遍次、迭代轮、位置（标签+下标）、原指令与原因。

## 语义等价保证（保守策略）

- 编译期折叠复用解释器的 `eval_binop`，64 位有符号**溢出环绕**语义与运行时完全一致，折叠不会在编译期触发异常。
- **除零不在编译期求值**：折叠遇到 `div`/`mod` 且除数为常量 0 时放弃折叠，把陷阱留给运行时原样抛出；`div`/`mod` 指令也永不作为死代码删除。
- 分支裁剪只在条件被证明为常量时进行，被裁掉的分支运行时必然不执行。
- 删除带标签的指令时自动把标签迁移到后继指令（或末尾补 `halt`），保证跳转目标有效。
- 对拍验证：`run_tests.py` 用解释器分别执行优化前/后程序，比较运行状态（halt/trap/step_limit）、全部输出与陷阱信息；内置 9 个结构化用例 + 300 个随机程序模糊对拍。
- **双方均超时（step_limit）时按输出前缀比较**：优化会改变每次循环迭代执行的指令条数，相同步数上限内两侧产生的输出条数可以不同；此时只要求较短的一侧是较长一侧的前缀（`irfold/interp.py` 的 `equivalent`）。其余情形状态、全部输出与陷阱信息必须完全相等。
- 随机程序生成器以前向跳转为主，同时以约 1/3 概率产生指向已定义标签的**回边（循环）**，程序可能不终止（由解释器步数上限兜底），从而覆盖循环内的常量折叠与双方超时情形。

## 目录结构

- `irfold/ir.py` — IR 定义、文本解析、CFG/uses/defs
- `irfold/interp.py` — 解释器（int64 环绕、C 风格截断除法、除零 Trap、步数上限）与对拍等价判据 `equivalent`
- `irfold/optimize.py` — 常量传播/折叠、分支裁剪、不可达删除、死代码删除、不动点驱动
- `irfold/programs.py` — 测试程序（循环内常量、嵌套分支、全部不可达、自跳转、除零、溢出、双方超时前缀比较等）
- `run_tests.py` — 对拍 + 结构断言 + 模糊测试 + 统计输出
- `stats.md` — 优化前后语句数与耗时统计（由脚本生成）
- `deletion_log_sample.json` — 删除记录样例（由脚本生成）

## 运行

```bash
python3 run_tests.py
```

## 库用法

```python
from irfold import parse, run, optimize

prog = parse("const x, 2\nbinop y, add, x, 3\nprint y\nhalt")
opt, log, iters = optimize(prog)
print(opt.dump())   # print 5\nhalt
print(run(prog)["outputs"], run(opt)["outputs"])  # [5] [5]
print(log)          # 每条删除/折叠记录
```

## IR 语法

```
label:  const dst, VALUE        # 常量
        input dst               # 读输入（耗尽后读 0）
        mov dst, src
        binop dst, OP, a, b     # OP: add sub mul div mod lt le gt ge eq ne
        print src
        jmp LABEL
        cjmp cond, L1, L2       # cond != 0 跳 L1
        halt
```

操作数为变量或整数字面量；读未定义变量得 0；`#` 后为注释。
