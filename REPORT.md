# 字节码解释器重构报告

## 1. 重构内容

| | 重构前 `vm/legacy_vm.py` | 重构后 `vm/table_vm.py` |
|---|---|---|
| 分发方式 | `run()` 内 27 个 `elif op ==` 分支 | `DISPATCH` 指令表 + `@instruction` 注册 |
| 执行状态 | `run()` 的 10 个局部变量被全部分支隐式共享 | `Context` 对象显式传入每个处理函数 |
| 新增指令 | 在 201 行的巨型函数里加分支，直接读写共享局部变量 | 一处实现（`op_xxx(ctx, arg)`）+ 一处注册（`@instruction("xxx")`） |
| 栈/类型检查 | 每个分支内联重复 | `_require_stack` / `_pop2_arith` 等共享辅助函数 |

**新增一条指令的改动位置（重构后仅两处，且相邻）：**

```python
@instruction("shl")          # 一处注册
def op_shl(ctx, arg):        # 一处实现
    a, b = _pop2_arith(ctx)
    ctx.stack.append(a << b)
```

主循环、异常处理、结果封装均无需改动。

## 2. 行为等价性验证（对拍）

`tests/difftest.py` 对同一批程序 + 输入同时跑两个实现，逐项比较
`Result(ok, value, error, steps, output)` —— 即执行结果、输出序列、
错误类型、执行步数四项完全一致：

```
对拍完成: 539/539 通过（手工用例 39 条 + 随机用例 500 条）
```

- 39 条手工用例（`tests/programs.py`）：覆盖全部 28 条指令、全部 11 种错误类型
  （`ZeroDivisionError` / `UndefinedVariable` / `StackUnderflow` / `StackImbalance` /
  `InvalidJump` / `UncaughtException` / `InputExhausted` / `TypeError` / `UnknownOp` /
  `HandlerUnderflow` / `StepLimitExceeded`）。
- 500 条固定种子随机程序（含越界跳转、未知指令、未定义变量、栈不平衡等），
  步数上限 2000，保证可终止。

## 3. 异常与跳转语义

`tests/test_controlflow.py` 共 26 个测试，逐条断言精确结果（两个实现各跑一遍）：

- **异常处理**：`try`/`throw` 捕获并恢复栈深度（`try_stack_restore`）、
  嵌套 try 内层抛向外层（`nested_try`）、运行时错误（除零、未定义变量、
  非法跳转）可被捕获且压栈错误类型名、`endtry` 后处理器弹出再抛则未被捕获
  （`throw_after_endtry`）、未捕获异常报错 `UncaughtException`。
- **提前返回**：分支内 `halt` 立即结束并做栈平衡检查（`early_return_neg/pos`）。
- **无条件跳转**：跳过会除零的死代码（`jmp_over_dead_code`）、
  越界/负地址报 `InvalidJump`、可被 `try` 捕获（`invalid_jump_caught`）。
- **栈不平衡**：结束时栈深 ≠ 1 报 `StackImbalance`
  （`imbalance_extra` / `imbalance_empty` / `falloff_imbalance`）。
- **步数上限**：死循环触发 `StepLimitExceeded`，且步数计数两版一致。

## 4. 耗时与结构对比（`python3 bench.py`，Python 3.12，5 次取最优）

| 程序 | 重构前 | 重构后 | 比值 |
|---|---:|---:|---:|
| sum_loop(20000)，260010 步 | ~26.5 ms | ~38.7 ms | ~1.45x |
| fib_iter(20000)，340014 步 | ~35.0 ms | ~51.8 ms | ~1.47x |
| throw_loop(5000)，55008 步 | ~8.0 ms | ~10.5 ms | ~1.31x |

| 结构指标 | 重构前 | 重构后 |
|---|---:|---:|
| 有效代码行数 | 214 | 210 |
| 函数数量 | 3 | 38 |
| 最长函数行数 | 201（`run`，含全部分支） | 30（`run`，仅主循环） |
| 分发分支数（`elif op ==`） | 27 | 0 |
| 指令表注册数 | 0 | 28 |

结论：指令表化带来约 1.3–1.5 倍的解释开销（Python 每条指令多一次函数调用），
换来的是：单点注册、处理单元相互隔离、上下文显式化、主循环与指令语义解耦。
对字节码解释器而言这通常是合理取舍；若需追平性能，可在主循环做
superinstruction 合并或改用代码生成，但那超出本次重构范围。

## 5. 运行命令

```bash
python3 tests/difftest.py          # 逐指令对拍（39 手工 + 500 随机）
python3 tests/difftest.py -v       # 打印每条用例的两版结果
python3 tests/test_controlflow.py  # 异常/跳转/栈不平衡专项断言
python3 bench.py                   # 耗时 + 结构对比
```
