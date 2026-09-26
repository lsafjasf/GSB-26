# 分块读取大文件：缺陷修复与回归测试报告

## 文件清单

| 文件 | 说明 |
|---|---|
| `chunked_reader_buggy.py` | 原始（含四类现网缺陷）实现，仅用于复现 |
| `chunked_reader.py` | 修复后的实现（纯标准库，Python 3） |
| `test_chunked_reader.py` | 4 个缺陷复现测试 + 13 个修复回归/逐字节对拍测试 |
| `benchmark.py` | 不同块大小的读取吞吐基准 |

## 四类缺陷与修复

| # | 现网缺陷 | 根因（buggy 实现） | 修复方式 |
|---|---|---|---|
| 1 | EOF 短读时重复处理上一块残留字节 | `readinto` 后不按实际字节数切片，直接拼接整个缓冲区 | 按 `read` 实际返回的 `bytes` 拼接，长度天然等于实际读到的字节数 |
| 2 | 读取中途被截断/IO 出错时异常未捕获 | 未对 `read` 做任何异常处理 | 捕获 `OSError`，转换为可区分的 `ChunkedIOError`，携带 `bytes_read` 与原始异常 |
| 3 | 跨块边界偏移错位 | 偏移按 `chunk_size` 累加并强制 `seek`，短读时跳过未读字节 | 顺序读取，不再手动维护偏移/seek；短读时循环补齐当前块 |
| 4 | 最后一个不完整块被静默丢弃 | 用 `size // chunk_size` 计算块数，余数部分从不读取 | 以 EOF 为终止条件，末块无论多短都照常产出 |

## 修复后 API 约定

- `read_chunked(source, chunk_size) -> ReadResult`：`source` 为路径或二进制文件对象。
- 成功：`result.ok == True`，`result.data` 与文件内容**逐字节一致**。
- 失败（不抛异常、不静默丢弃）：
  - `result.error` 为 `TruncatedReadError`（读取中被截断）或 `ChunkedIOError`（底层 IO 错误），二者均为 `ChunkedReadError` 子类，可区分；
  - `result.bytes_read` 为失败前已成功读取的字节数，`result.data` 为对应的部分数据。
- `iter_chunks(source, chunk_size)`：逐块产出，末块不完整也产出；IO 错误以 `ChunkedIOError` 抛出。
- `chunk_size < 1` 抛 `ValueError`。

## 测试覆盖（17 个用例，全部通过）

- 缺陷复现：末块丢弃、残留字节重复、偏移错位跳过字节、IO 异常未捕获。
- 逐字节对拍：大小 × 块大小矩阵（整除 / 不整除 / 文件小于一块 / 块大于文件）。
- 边界：空文件、单字节文件、块大小为 1、超大块（16 MiB 块读 1 MiB 文件）、`chunk_size=0`。
- 模拟故障：短读（单次 read 最多 5 字节仍逐字节正确）、中途抛 `OSError`、
  读取中真实 `os.truncate` 截断 —— 均返回可区分错误并指出已读字节数。

## 吞吐基准（本机，256 MiB 文件，页缓存热数据，5 次取最优）

| 块大小 | 吞吐 | 耗时 |
|---|---|---|
| 4 KiB | ~907 MiB/s | 282 ms |
| 16 KiB | ~1162 MiB/s | 220 ms |
| 64 KiB | ~1193 MiB/s | 215 ms |
| 256 KiB | ~1214 MiB/s | 211 ms |
| 1 MiB | ~1228 MiB/s | 208 ms |
| 4 MiB | ~1203 MiB/s | 213 ms |
| 16 MiB | ~1093 MiB/s | 234 ms |

（数据为单次运行示例，不同机器会有差异；可用 `python3 benchmark.py` 复测。）

## 块大小取舍

- **过小（≤4 KiB）**：系统调用与 Python 循环开销占比升高，吞吐明显下降（本例约 -25%）。
- **64 KiB ~ 1 MiB**：进入平台期，吞吐接近上限，是通用场景推荐区间；默认取 64 KiB，
  内存占用小且吞吐损失可忽略。
- **过大（≥4 MiB）**：单块内存分配与拷贝成本上升、缓存局部性变差，吞吐反而回落；
  且流式处理时首块延迟变高、内存峰值 = 块大小。
- 结论：吞吐敏感选 256 KiB ~ 1 MiB；内存敏感或流式消费选 64 KiB（默认值）。

## 运行命令

```bash
cd /home/administrator/gsb/uid183/B
python3 -m unittest test_chunked_reader -v   # 复现 + 回归测试
python3 benchmark.py 256                     # 吞吐基准（参数为文件大小 MiB）
```
