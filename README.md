# GSB-26：事务撤销日志与回滚库（undolog）

纯 Python 3 标准库实现，无第三方依赖。

## 文件

- `undolog.py` — 库源码（`TxStore` 键值存储 + `Transaction` 事务）
- `test_undolog.py` — 自测：对拍、断点续滚、并发可见性、边界情形、性能

## 运行

```bash
python3 -m unittest -v test_undolog.py          # 全部 15 个测试
python3 -m unittest test_undolog.TestScaleAndPerf.test_perf_rollback_100k  # 只看性能
```

## 设计

**撤销日志**：每个写操作（`set`/`delete`）在应用前，先把逆操作（`old` 旧值 + `had` 是否存在）
以 JSON Lines 追加到磁盘日志（`begin/op/commit/rollback_begin/undone/rollback_done`）。
回滚按逆序应用逆操作，每步写 `undone` 标记，最后写 `rollback_done`。

**幂等**：应用旧值是天然幂等操作；`rollback()` 重复调用直接返回；
日志中 `rollback_begin`/`rollback_done` 只出现一次（有测试断言）。

**断点续滚**：
- 进程内失败：回滚抛异常后事务保持 `rolling` 状态，`resume()` 或再次
  `rollback()` 会跳过已完成步骤（内存中的 undone 集合）从断点继续。
- 进程崩溃：重新打开 `TxStore` 时 `recover` 重放日志——已提交事务重放新值，
  未提交或处于回滚中的事务逆序撤销，并补齐缺失的 `undone` 标记、
  写入 `rollback_done`。崩溃造成的尾部半行（撕裂写）被截断容忍。
- 测试覆盖：注入异常中断续滚、回滚中再次失败、`os._exit` 真杀进程后重启恢复、
  事务提交前崩溃自动 abort。

**对拍**：`test_against_snapshot_reference` 用 300 组随机种子，每组 1–6 个事务、
每事务 0–60 个随机写/删、随机提交/回滚，与「快照旧状态 + 整体恢复」的参照实现
`SnapshotRef` 逐事务比对。存储文件在整个用例期间持续存在：每个事务后随机
重启（关闭并重开 `TxStore`，走 `recover` 日志重放）再比对，用例结束最终
重启重放一次并断言磁盘日志非空，确保磁盘日志与重启恢复路径被真实覆盖。
`test_restart_continues_rollback` 覆盖「回滚中途失败即关闭、重启后断点续滚」。

## 并发可见性说明

模型为**单写入者 + 并发读者**：同一时刻只允许一个活跃事务（`begin` 冲突抛
`TxError`），回滚完成前也不允许开启新事务。

回滚期间，该事务触碰过的所有 key 进入 **quarantine（隔离集）**：

- `get(key)`：阻塞等待，直到回滚完成，返回回滚后的旧值；
- `try_get(key)`：快速失败，抛 `KeyQuarantined`；
- 两种读法都**绝不会读到回滚中间状态**（未受影响 key 的读写不受任何影响）；
- 回滚被异常打断时隔离保持有效，直到续滚完成；进程崩溃后重启时
  `recover` 在对外服务前完成回滚，不存在可见中间态的窗口。

`test_readers_never_see_intermediate_state` 验证：回滚暂停在中途时，4 个读线程
高频 `try_get` 只能得到旧值或 `KeyQuarantined`，阻塞式 `get` 在回滚完成前不返回。

## 性能数据（10 万次操作的回滚）

环境：Python 3.12，Linux x86-64，durable 模式（commit/rollback 标记均 fsync）。
三次运行取区间：

| 阶段 | 耗时 |
|---|---|
| 写入 100k 操作（记日志 + 应用） | 0.25–0.44 s |
| commit（fsync） | ~0.01–0.03 s |
| **回滚 100k 操作** | **0.23–0.32 s（约 31–43 万 ops/s）** |
| 重启 recover 重放整个日志 | 0.41–0.55 s |

精确数字由 `test_perf_rollback_100k` 在运行时打印。

## 已知限制

- 单写入者模型，不支持并发事务交错（读者并发安全）。
- 日志只追加不压缩，长期运行需自行做 checkpoint/截断。
- key 须为 str，value 须为 JSON 可序列化。
