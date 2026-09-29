# 多线程聚合统计缺陷修复报告

语言：Python 3（仅标准库）。环境：Python 3.12.3。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `src/aggregator_buggy.py` | 有缺陷的实现（仅用于复现，对应现网四类问题） |
| `src/aggregator.py` | 修复后的实现 `Aggregator` |
| `tests/test_buggy_reproduce.py` | 四类缺陷的稳定复现用例（断言“错误行为发生”） |
| `tests/test_aggregator.py` | 修复后的自洽 / 幂等 / 一致性 / 原子性断言 |
| `bench.py` | 修复前后多线程吞吐对比 |

## 运行命令

```bash
# 复现四类缺陷（4 个用例，断言缺陷存在）
python3 -m unittest discover -s tests -p 'test_buggy_reproduce.py' -v

# 修复后回归（8 个用例：自洽、幂等、去重命中记录、并发读一致、原子性、单调性）
python3 -m unittest discover -s tests -p 'test_aggregator.py' -v

# 全部测试
python3 -m unittest discover -s tests -v

# 吞吐对比
python3 bench.py
```

## 四类缺陷的根因与修复

1. **并发写入计入两次**：缺陷版样本去重为无锁 check-then-act
   （`if sid in seen` → 让权 → `seen.add`），并发下同一样本通过多次检查。
   复现：`test_1_concurrent_write_double_counted` 用屏障把 8 个线程同步在
   check 与 add 之间，确定性得到 `total == 8`（应为 1）。
   修复：所有共享状态由同一把 `threading.Lock` 保护，去重判断与落账在
   同一临界区内完成。

2. **批次重试计数翻倍**：缺陷版 `apply_batch` 完全忽略 `batch_id`。
   复现：`test_2_batch_retry_double_counted`，同一批次投递两次总数 14（应为 7）。
   修复：以 `batch_id` 为幂等键，落账前先查去重表，重复投递返回 `False` 不计数。

3. **统计输出读到中间态**：缺陷版 `snapshot` 无锁遍历字典，且 `total` 与
   维度计数分步更新。复现：`test_3_snapshot_reads_partial_state`，并发读写时
   观察到 `RuntimeError: dictionary changed size during iteration` 或
   `total != sum(dims)`。
   修复：`snapshot()`/`flush()` 在锁内一次性拷贝，读者只能看到某个完整
   落账点之后的状态。并发读断言：`test_snapshot_consistent_under_concurrency`
   在 8 写 1 读下校验每个快照 `sum(dims)==total`、`sum(cum_dims)==cum_total`
   且累计值单调不回退。

4. **失败批次部分计入**：缺陷版逐条落账，中途抛错时前面样本已计入。
   复现：`test_4_failed_batch_partially_applied`，批次第二条非法抛错后
   `total == 5`（应为 0）。
   修复：先整体校验（零副作用），再在临界区内一次性落账；校验失败不记录
   `batch_id`，修正后可用同一 `batch_id` 安全重投
   （`test_failed_batch_is_atomic`）。

## 幂等设计：批次标识与去重依据

- **批次标识**：调用方提供的 `batch_id`（建议上游使用“消息 ID / 任务 ID +
  批次序号”等全局唯一标识），作为唯一幂等键。
- **去重依据**：内存中去重表 `_seen_batches`（set）+ FIFO 队列
  `_batch_order`，容量有界（`dedup_capacity`，默认 100 万），超容按 FIFO
  驱逐最老记录。调用方需保证重试发生在该窗口内；窗口外到达的极端延迟重试
  不再保证幂等。
- 去重判断与落账在同一临界区，多线程同时重投同一批次也只计一次
  （`test_concurrent_retry_storm_idempotent`）。
- 校验失败的批次**不**占用幂等键，修正内容后可安全重投。
- **去重命中记录**：每次重复投递命中去重表时，在该批次的记录上累计
  `hits` 并记录命中时的提交序号 `last_hit_commit`；`dedup_report()`
  输出每批的 `{applied_commit, hits, last_hit_commit}`，与统计读取共用
  同一把锁，输出本身也是一致快照。批次记录随去重表 FIFO 驱逐一并清除。
  断言：`test_dedup_hit_records_per_batch`（逐批核对命中次数与提交序号）、
  `test_dedup_hit_records_concurrent_retry_storm`（8 线程重投风暴下
  命中总数严格等于重复投递次数）。

## 一致快照读取语义

- **提交边界**：每次成功落账构成一次提交，分配单调递增的 `commit_seq`
  （从 1 开始）；重复投递与校验失败的批次不产生提交。
- **快照语义**：`snapshot()` 在锁内一次性拷贝，返回的始终是某次提交之后
  的完整状态并携带 `commit_seq`——读者要么看到第 k 次提交的完整状态、
  要么看到第 k+1 次提交的完整状态，绝不出现部分更新的中间态。
- **并发读断言**：`test_snapshot_consistent_under_concurrency` 在 8 写 1 读
  下校验每个快照：`sum(dims)==total`、`sum(cum_dims)==cum_total`、
  `commit_seq` 与 `cum_total` 单调不回退、所有计数非负；写完后
  `commit_seq == 成功批次数`、`cum_total == 期望总和`。

## 自洽与单调性保证

- 统计总数严格等于成功批次样本数之和：`test_concurrent_writes_exact_total`
  （8 线程 × 500 批，最终 `cum_total == total == 期望和`）。
- 任一维度计数不为负、不回退：维度计数只增不减；`flush()` 原子换出窗口并
  清零，累计值 `cum_*` 单调不减；`test_flush_monotonic_no_negative_no_regression`
  校验所有窗口增量非负、累计单调、增量之和等于累计值。

## 吞吐对比（修复前后）

工作负载：batch_size=8，每线程 30000 批，共享同一聚合器实例，3 次取中位数。

| 实现 | 线程数 | samples/s | 耗时(s) |
| --- | ---: | ---: | ---: |
| buggy | 1 | 10,238,347 | 0.023 |
| fixed | 1 | 8,201,326 | 0.029 |
| buggy | 4 | 9,852,250 | 0.097 |
| fixed | 4 | 7,718,936 | 0.124 |
| buggy | 8 | 8,581,995 | 0.224 |
| fixed | 8 | 7,958,738 | 0.241 |
| buggy | 16 | 9,638,105 | 0.398 |
| fixed | 16 | 7,780,373 | 0.494 |

结论：修复版吞吐约为缺陷版的 **76%–93%**（单线程约 -20%，16 线程约 -19%），
代价来自锁、整批校验与去重表查询；在 CPython GIL 下临界区极短（纯 dict 操作），
随线程数增加未出现明显锁竞争塌陷。缺陷版的高吞吐是以丢失更新、重复计数、
中间态可读为代价换来的，不具备可比性。若需进一步提速，可按维度分片加锁
（sharding）或改用批量预聚合后单点合并，当前单锁实现已满足正确性优先的目标。
