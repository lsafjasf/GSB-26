# tasktree — 任务树与取消传播库（Python 3，仅标准库）

## 文件
- `tasktree.py` — 库源码（`TaskNode` / `State` / `Cancelled` / `Resource`）
- `test_tasktree.py` — 28 个自测：状态断言、资源断言、竞争时序、边界情形（含默认递归上限下的 10 万层深链）
- `perf_tasktree.py` — 十万级任务创建与取消传播性能自测

## 运行
```bash
python3 -m unittest test_tasktree -v   # 运行全部测试
python3 perf_tasktree.py               # 运行性能自测
```

## 设计要点
- **任务树**：`create_child()` 派生子任务，`wait_children()` 等待全部子任务，
  `cancel()` 取消单个任务并递归传播到整棵子树。
- **取消不可逆**：状态迁移在同一把 `RLock` 下原子裁决。任务进入 `CANCELLED` 后，
  `set_result`/`set_exception` 抛出 `Cancelled`，完成回调被清空且永不触发；
  已取消父任务下新建的子任务直接继承取消态。
- **取消 ≠ 失败**：`CANCELLED` 与 `FAILED` 是不同终态，错误类型分别为
  `Cancelled` 与业务异常；`summary()` 汇总整棵子树的取消/失败/完成计数与失败详情。
- **无递归深度限制**：取消传播与 `summary()` 遍历均为显式栈的迭代实现，
  任意深度的任务链在默认递归上限（1000）下即可运行，无需调整 `sys.setrecursionlimit`。
- **资源确定释放**：`Resource.release()` 幂等；任务进入任意终态时释放全部已注册
  资源，终态后注册的资源立即释放；重复取消 / 取消已完成任务均为安全 no-op。

## 性能数据（本机 Python 3.12）
| 场景 | 创建 10 万任务 | 取消传播 10 万任务 |
|---|---|---|
| 扁平树（1 根 + 10 万子任务） | ~486 ms | ~106 ms |
| 深链（10 万层嵌套） | ~382 ms | ~114 ms |
| 混合树（100 子树 x 1000） | ~364 ms | ~105 ms |
