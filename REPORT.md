# 外部排序缺陷修复报告

## 实现与文件

- `src/external_sort.py` —— 修复后的实现（仅标准库）。
- `src/buggy_external_sort.py` —— 刻意保留四类缺陷的旧实现，仅作复现对照。
- `tests/test_external_sort.py` —— 16 个用例：缺陷复现 + 性质断言 + 边界情形 + 归并不变量。
- `tests/memory_benchmark.py` —— 内存峰值基准。

## 四类缺陷的根因与修复

| 缺陷 | 根因（旧实现） | 修复 |
|---|---|---|
| 同键记录归并丢失 | 归并用 dict 按 key 暂存各块当前记录，同键互相覆盖 | 最小堆，元素为 `(key, chunk_index, record)`，每个读取器至多占一个槽位，弹出即补 |
| 异常后临时文件残留 | 临时文件散落工作目录，仅在正常结束路径删除 | `tempfile.mkdtemp` 专属目录 + `try/finally` 中无条件 `shutil.rmtree` |
| 块边界记录重复 | 分块循环把边界记录 carry 到下一块再写一次 | `for line in src` 顺序消费，每条记录恰好进入一个块，无进位逻辑 |
| 稳定性名不副实 | 块内 `chunk.sort()` 按整行字典序，同键被记录内容重排 | 块内 `list.sort(key=...)`（稳定），归并同键按 `chunk_index` 决胜 |

## 归并如何取最小键 & 稳定性论证

堆元素为三元组 `(key, chunk_index, record)`，`heapq` 每次弹出元组最小者：

1. 主序是 `key` —— 弹出的即当前各块头部中键最小者；
2. `key` 相同时由 `chunk_index` 决胜 —— 块编号单调对应记录在原始输入中的先后；
3. `record` 永不参与比较（前两项已能定序），同键不会被记录内容重排。

稳定性不变量（均有对应断言）：

- **I1（块内）**：`list.sort` 稳定，同键记录在块文件中的顺序 = 输入相对顺序。
- **I2（块间）**：同键记录按 `chunk_index` 升序输出；每块在堆中至多一条，块内顺序即文件顺序。由 I1+I2，同键输出顺序 = 原始输入相对顺序（`test_stability_preserves_original_relative_order`、`test_all_keys_equal`、`test_heap_tie_break_by_chunk_index`）。
- **I3（守恒）**：写出条数 = 各块记录数之和 = 输入条数（`test_merge_count_equals_sum_of_chunk_counts`、`test_conservation_many_duplicate_keys`，含 `Counter` 多重集相等断言）。

## 内存峰值数据（Python 3.12，记录 ~64 字节，tracemalloc 实测）

实验 A：输入固定 200,000 条，块大小变化：

| chunk_size | 峰值 |
|---|---|
| 1,000 | 2.73 MiB（归并扇入 200 个读取器，成为主导项） |
| 10,000 | 1.71 MiB |
| 50,000 | 8.50 MiB |
| 200,000 | 33.79 MiB |

实验 B：块大小固定 10,000，输入总量变化：

| 输入条数 | 峰值 |
|---|---|
| 50,000 | 1.71 MiB |
| 200,000 | 1.71 MiB |
| 800,000 | 1.72 MiB |

结论：峰值内存 ≈ `chunk_size × 单条记录驻留开销`（实测约 175–180 字节/条，
含 str/list 对象头）与 `归并路数 × 单读取器开销`（约 13 KB/路，文件缓冲 +
迭代器）两者中的较大者。块足够大时峰值与输入总量无关（实验 B 完全平坦）；
块过小时归并路数膨胀反而抬高峰值（实验 A 第一行）。工程上按可用内存
反推 `chunk_size ≈ 内存预算 / 180B`，并保证 `输入条数 / chunk_size` 不超过
文件描述符与内存允许的路数即可。

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 全部回归测试（16 个用例）
python3 tests/memory_benchmark.py          # 内存峰值基准
```
