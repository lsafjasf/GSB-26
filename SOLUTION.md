# 外部排序资源治理迭代说明

## 文件

- `external_sort_buggy.py` — 有缺陷的原始实现（保留用于复现，勿改）
- `external_sort.py` — 实现：分块落盘 + 多轮 k 路归并 + 降级/清理治理
- `test_reproduce.py` — 复现四类线上问题（对 buggy 实现全部通过，4 用例）
- `test_external_sort.py` — 守恒/稳定/异常安全基础断言（9 用例）
- `test_resource_governance.py` — 本次迭代：多轮归并、降级、异常清理、对拍（15 用例）
- `memory_benchmark.py` — 峰值内存 chunk_size × merge_ways 二维基准（含隔离子进程 maxrss）
- `memory_peak_2d.csv` — 二维明细（真实跑出）
- `run_verification.sh` — 一键复跑，输出落盘 `verification_output.txt`

## 运行

```bash
./run_verification.sh                                  # 一键复跑全部
python3 -m unittest test_resource_governance -v        # 本次迭代测试
python3 memory_benchmark.py --csv memory_peak_2d.csv   # 二维内存数据
```

## 本次迭代（资源治理：从“块大小”一条轴扩到临时文件 + 内存）

### 1. 归并路数轴 `merge_ways`（多轮 k 路归并）

- 新增参数 `merge_ways`（>=2），限制每轮同时打开的 run 文件句柄数与
  归并堆大小；run 数超过路数时自动多轮归并（`_merge_round`），单 run 组直通。
- 归并堆元素仍是 `(key, run_idx, line, stream)`，跨轮同样保持稳定：
  每轮产物内稳定，下一轮各输入组按起始 run 位置排序编号，并列时
  run_idx 小者先出。归并内保留键单调不减断言。
- 返回 `SortResult(backend, num_runs, merge_rounds, merge_ways)` 可观测信息。

### 2. 临时目录不可写：两条明确的降级路径（`on_temp_error`）

- `"fail"`（默认，兼容旧契约）：临时目录创建失败或写 run 文件失败时，
  抛 `TempDirUnavailableError`（`OSError` 子类），不留临时文件。
- `"memory"`：同一异常触发纯内存稳定排序 `_in_memory_sort`，
  返回 `backend=="memory"`，不使用任何临时文件。
- 错误分类：只有临时存储侧（建临时目录、写中间 run、中间轮读写）的
  `OSError` 才标记为 `TempDirUnavailableError`；最终输出文件不可写
  原样抛 `OSError`，不会被误判成“临时目录不可用”。
- 测试同时覆盖真实只读目录（非 root）与注入失败（root/CI 也能稳定复现），
  以及首个 run flush 中途写失败的降级。

### 3. 异常后自动清理（含多轮归并中途）

- 所有 run 文件位于 `TemporaryDirectory` 托管目录内，任何异常路径都由其
  递归清理，天然覆盖 split 失败、中间轮失败、最终轮失败。
- 每轮中间归并成功后立即显式删除被消费的旧 run，把磁盘占用控制在
  “一轮”量级；归并中途异常时打开的输入 run 由 `_merge_streams` 的
  `finally` 关闭，半成品由托管目录兜底。
- 测试在中间轮与最终轮分别注入异常（按 key 调用计数定点爆炸），
  断言异常后临时目录/`.run` 文件零残留。

### 4. 降级路径与正常路径对拍

`test_resource_governance.DifferentialFallbackTests` 对
{单条、键全异、大量重复键、完全相同记录、空输入} × {chunk=1/37/1000,
ways=2/3/8} 组合，比较磁盘路径与内存降级路径输出的**字节级一致性**，
并断言各自的 backend 元信息正确。

## 峰值内存二维数据（10 万条 × 82B，真实输出）

`py_peak` = tracemalloc 排序期间 Python 堆峰值（MB）：

| chunk_size | runs | ways=2 | ways=4 | ways=8 | ways=16 | ways=32 |
|-----------:|-----:|-------:|-------:|-------:|--------:|--------:|
| 1,000      | 100  | 0.200 | 0.193 | 0.216 | 0.388 | 0.738 |
| 5,000      | 20   | 0.853 | 0.853 | 0.853 | 0.853 | 0.853 |
| 20,000     | 5    | 3.368 | 3.368 | 3.367 | 3.368 | 3.367 |
| 50,000     | 2    | 8.409 | 8.408 | 8.408 | 8.408 | 8.408 |
| 100,000    | 1    | 16.708 | 16.708 | 16.708 | 16.708 | 16.708 |

归并轮数：

| chunk_size | ways=2 | ways=4 | ways=8 | ways=16 | ways=32 |
|-----------:|-------:|-------:|-------:|--------:|--------:|
| 1,000 (100 runs)  | 7 | 4 | 3 | 2 | 2 |
| 5,000 (20 runs)   | 5 | 3 | 2 | 2 | 1 |
| 20,000 (5 runs)   | 3 | 2 | 1 | 1 | 1 |
| 50,000 (2 runs)   | 1 | 1 | 1 | 1 | 1 |
| 100,000 (1 run)   | 0 | 0 | 0 | 0 | 0 |

结论：

- 峰值模型 **O(chunk_size + merge_ways)**：分块缓冲区按 chunk_size 线性
  增长（5k→100k：0.85→16.7MB，约 20 倍），主导峰值；
- merge_ways 轴只在 chunk 很小、run 很多时显现：chunk=1,000 时
  ways 从 2→32，py_peak 0.20→0.74MB（归并堆 + 每路一个文件读缓冲随
  路数增加）；chunk≥5,000 时分块缓冲区淹没该效应；
- 增大 merge_ways 的治理收益是**轮数与 IO 趟数下降**（100 runs：
  ways=2 需 7 轮、ways=32 只需 2 轮），代价是句柄与归并缓冲上升；
- 选型：按内存预算定 chunk_size（≈ M/(3·s)，s 为单条字节），按文件
  句柄/缓冲预算定 merge_ways，两者共同把峰值压在 O(chunk_size+merge_ways)。

注：`maxrss` 用隔离子进程逐格测量（ru_maxrss 单调不减，同进程连跑会让
后格继承前格峰值而失真）；该列含解释器基线，二维明细见 `memory_peak_2d.csv`。

## 上一轮四类缺陷（仍保持修复）

| # | 缺陷 | 修复 |
|---|------|------|
| 1 | 相同键归并丢失 | 归并不去重，每条恰好弹出一次 |
| 2 | 异常后临时文件残留 | TemporaryDirectory 托管 + finally |
| 3 | 块边界记录重复 | flush 后 `buf.clear()` |
| 4 | 声称稳定实际不稳定 | 堆元素 `(key, run_idx, ...)`，并列取 run_idx 小者 |
