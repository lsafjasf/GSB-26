# event-sourcing-lib（纯标准库 Python 3）

一个最小事件溯源库：状态由事件流重放推导，支持幂等重放、断点续放（强杀后恢复）、
多投影互检。仅使用 Python 3 标准库。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

## 结构

- `event_sourcing/events.py` — 不可变 `Event`：`event_id`（全局唯一，去重依据）、
  `stream_id`（聚合）、`seq`（流内逻辑序号）、`type`、`version`、`data`。
- `event_sourcing/store.py` — `EventStore`：JSONL 追加日志。`append()` 幂等
  （重复 `event_id` 直接忽略）；`read_all()` 返回**规范顺序** `(stream_id, seq, position)`，
  乱序到达不影响重放结果。
- `event_sourcing/projections.py` — `Projection` 基类 + 两个投影：
  `DetailViewProjection`（账户交易明细视图）与 `AggregateViewProjection`
  （余额/计数聚合视图）；含 upcaster 注册机制与 `view_inconsistencies()` 互检函数。
- `event_sourcing/replay.py` — `Replayer`：逐事件重放，每事件落一次**原子检查点**
  （临时文件 + `os.replace`），记录最后处理事件的**标识边界** `(stream_id, seq)`、
  `processed_count`（已处理事件计数，仅作信息记录）、`seen_event_ids` 与状态快照；
  `kill_after` 用于测试中模拟强杀。

## 关键设计

- **去重依据**：生产方分配的全局唯一 `event_id`。两道防线：存储层 `append()`
  忽略重复 id；投影层 `apply()` 用 `seen_event_ids` 跳过已处理事件，重复投递不改变状态。
- **乱序到达**：重放不依赖到达顺序，而按规范顺序 `(stream_id, seq)` 排序，
  因此同一日志内容的重放结果确定唯一。
- **强杀恢复**：检查点与状态原子提交；续放边界是最后处理事件的标识
  `(stream_id, seq)`，而**不是事件条数/下标**——停机期间若有迟到事件补进日志
  并排在边界之前，下标切片 `events[count:]` 会把新事件永久跳过。重启时按标识
  在规范序中定位边界；若边界之前出现了未见过的事件（停机期间插入的迟到事件），
  则重置投影并从头重建。对拍测试在多个随机关杀点及「停机期间补进迟到事件」
  场景下验证：续放结果与从头完整重放**逐项完全一致**。
- **版本兼容**：投影声明 `supported_versions`；旧版本事件经 upcaster 链
  （如 `Deposited v1 -> v2`）升级后处理；无 upcaster 的未知版本抛出
  `IncompatibleEventVersionError`。

## 测试覆盖（tests/test_event_sourcing.py）

- 空事件流、单事件、大量事件（1500+）
- 乱序到达 == 顺序到达（状态逐字节相等）
- 幂等：重复 append / 重复 apply / 完成后重跑均为 no-op（断言状态不变）
- 强杀对拍：20+ 个随机关杀点 × 2 个投影，续放结果与全量重放一致
- 停机期间补进迟到事件（新流排在已处理边界之前）：按 `(stream_id, seq)`
  标识定位边界并触发重建，续放结果与从头重放逐项相等
- 版本：v1 旧事件 upcast 正确、v99 不兼容报错、未知事件类型报错
- 每个场景都做双投影互检（明细求和 == 聚合余额、笔数一致），并与独立
  参考模型 `expected_model()` 对账
