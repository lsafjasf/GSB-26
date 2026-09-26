# 外部排序缺陷修复说明

## 文件

- `external_sort_buggy.py` — 有缺陷的原始实现（保留用于复现，勿改）
- `external_sort.py` — 修复后的实现
- `test_reproduce.py` — 稳定复现四类线上问题的测试（对 buggy 实现全部通过）
- `test_external_sort.py` — 修复版的守恒/稳定性/异常安全断言（9 个用例）
- `memory_benchmark.py` — 内存峰值基准

## 运行命令

```bash
python3 -m unittest test_reproduce -v      # 复现四类缺陷（buggy 实现）
python3 -m unittest test_external_sort -v  # 修复版性质测试
python3 memory_benchmark.py                # 内存峰值数据
```

## 四类缺陷与修复

| # | 缺陷 | 根因（buggy） | 修复 |
|---|------|----------------|------|
| 1 | 相同键记录归并时丢失 | `_read_head` 把同 run 内与上一条同键的记录当“重复”跳过 | 归并不去重，每条记录恰好弹出一次 |
| 2 | 异常后临时文件残留 | 只有正常结束才 `os.unlink`，且无 try/finally | `tempfile.TemporaryDirectory` 托管，正常/异常都清理 |
| 3 | 块边界记录重复写出 | flush 后 `buf = buf[-1:]` 保留了上一条 | flush 后 `buf.clear()` |
| 4 | 声称稳定实际不稳定 | 并列时 `<=` 选取更晚的 run | 堆元素 `(key, run_idx, ...)`，并列取 run_idx 小者 |

## 归并最小键选取与稳定性

最小键用最小堆（`heapq`）选取，堆元素为 `(key, run_idx, line, file)`：

- 主序 `key`：保证弹出的 key 序列单调不减（merge 内有不变量断言
  `assert last_key is None or not k < last_key`）；
- 次序 `run_idx`：run 按输入顺序编号，key 并列时 run_idx 小者（输入位置更早）
  先弹出，保证跨 run 稳定；
- run 内部：`list.sort` 是稳定排序，块内相同键保持输入相对顺序；
- `(key, run_idx)` 对每个堆元素唯一（每个 run 在堆中至多一条），
  因此堆序全序无歧义，不会退化为比较 line/file。

两条合起来 ⇒ 全局稳定。测试侧不变量断言：

- 守恒：`len(out) == len(records)`；
- 稳定+有序：`out == sorted(records, key=key)`（Python sorted 为稳定排序）；
- 逐键相对顺序：输出中每个键的记录序列与输入中该键的记录序列逐项相等。

## 覆盖的边界情形

空文件、单块（n < chunk_size）、单记录、全部键相同、chunk_size=1、
大量重复键、归并中途异常、临时目录只读。

## 内存峰值数据

`python3 memory_benchmark.py`：100,000 条 × 82B 记录（共 8.2MB），
`py_peak` 为 tracemalloc 统计的排序期间 Python 堆峰值：

| chunk_size | run 数 | py_peak (MB) | maxrss (MB) |
|-----------:|-------:|-------------:|------------:|
| 1,000      | 100    | 2.2          | 33.7        |
| 5,000      | 20     | 0.9          | 33.7        |
| 20,000     | 5      | 3.4          | 33.7        |
| 50,000     | 2      | 8.4          | 33.7        |
| 100,000    | 1      | 16.7         | 48.7        |

结论：

- 峰值内存 ≈ `chunk_size` 条记录的分块缓冲区 + run 数条记录的归并堆
  （含每 run 一个文件读缓冲），即 **O(chunk_size + num_runs)**；
- chunk_size 主导：5k→100k 时峰值从 0.9MB 线性涨到 16.7MB；
- chunk_size 过小（如 1k）时 run 数膨胀，归并阶段 100 个堆元素 +
  100 个文件读缓冲反而抬高峰值（2.2MB > 5k 时的 0.9MB）；
- 实践中按可用内存预算选取 chunk_size，例如限制峰值 M 字节、
  单记录约 s 字节（含 Python 对象开销约 2-3 倍），取 `chunk_size ≈ M / (3s)`。
