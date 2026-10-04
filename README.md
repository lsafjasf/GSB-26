# framing — 流式协议分帧库（纯标准库 Python 3）

按字节流增量喂入，产出完整消息；长度前缀与分隔符两种分帧方式；
结果与切分方式无关；长度与缓冲均有硬上限，恶意输入立即报错并指出帧偏移。

## 文件

- `framing.py` — 库源码（`LengthPrefixFramer` / `DelimiterFramer`）
- `test_framing.py` — 切分等价性对拍 + 边界用例集（23 个用例）
- `bench_buffer.py` — 缓冲峰值测量（含 tracemalloc 进程峰值）

## 运行

```sh
python3 test_framing.py -v   # 自测
python3 bench_buffer.py      # 内存峰值数据
```

## 用法

```python
from framing import LengthPrefixFramer, DelimiterFramer

f = LengthPrefixFramer(max_frame_size=1 << 20)   # 4 字节大端长度前缀
wire = f.encode(b"hello") + f.encode(b"")        # 编码（含零长度包）
msgs = f.feed(wire[:3]) + f.feed(wire[3:])       # 任意切分喂入

d = DelimiterFramer(delimiter=b"\n", max_frame_size=1 << 20)
msgs = d.feed(b"a\nb\nc")                        # -> [b"a", b"b"]，b"c" 留缓冲
```

## 缓冲上界与超限行为

| 分帧器 | 内部缓冲上界 | 超限行为 |
|---|---|---|
| LengthPrefix | `header_size + max_frame_size + 单次feed` | 声明长度超限 → `FrameTooLargeError`（含流偏移），缓冲清零 |
| Delimiter | `max_buffer + 单次feed`（默认 `max_frame_size + len(delimiter)`） | 分隔符迟迟不出现 → `BufferLimitExceededError`，缓冲清零 |

报错后缓冲自动清空，即使调用方继续喂入也不会无限增长；复用前调 `reset()`。
`framer.buffer_peak` 可随时读取历史缓冲峰值。

实测（12.5 MiB 流、64 KiB 帧上限，详见 `bench_buffer.py` 输出）：
逐字节喂入时缓冲峰值 65540 B（= 上界），进程峰值 0.19 MiB；
1 MiB 大块喂入时峰值 ≈ 上界 + 1 MiB，与理论一致。

## 设计说明

- 分隔符协议下，包体内的分隔符字节即帧边界（协议语义）；需要包体
  内嵌任意字节（含分隔符）时请用长度前缀分帧，或在上层做转义。
- 错误中的 `offset` 是出错帧在整个流中的绝对字节偏移，跨多次 feed 累计。

---

# query_engine — 分片并行查询执行器（纯标准库 Python 3）

在串行执行器基础上加了分片并行：扫描按**连续有序分片**下发到
`multiprocessing` worker，归并严格按分片下标顺序进行，因此合并结果与
串行结果**逐行一致**（对拍见 `results/diff_report.txt`，指纹含行序）。

## 文件

- `query_engine.py` — `execute_serial` / `execute_parallel` / `ParallelExecutor`
- `test_query_engine.py` — 并行 vs 串行对拍回归（17 个用例）
- `diff_check.py` — 场景 × 并行度对拍，输出结果指纹（sha256，含行序）
- `bench_parallel.py` — 不同并行度耗时基准（一次性池 vs 常驻池）
- `results/diff_report.txt` / `results/bench_results.txt` — 实测数据

## 运行

```sh
python3 test_query_engine.py -v     # 回归对拍
python3 diff_check.py               # 对拍数据（指纹 + PASS/FAIL）
python3 bench_parallel.py 1000000   # 并行度耗时数据
```

## 用法

```python
from query_engine import Aggregate, Filter, Query, ParallelExecutor, execute_parallel

q = Query(filter=Filter("amount", "ge", 1000), aggregates=(
    Aggregate("count"), Aggregate("sum", "amount"),
    Aggregate("min", "amount"), Aggregate("max", "amount"),
    Aggregate("distinct", "region"),
))
execute_parallel(rows, q, workers=8)          # 一次性：每次查询新建进程池

with ParallelExecutor(workers=8) as ex:       # 常驻池：bind 一次，反复查询
    ex.bind(rows)
    ex.execute(q)                             # 只传输下标+查询描述
```

## 聚合口径（跨分片边界）

| 聚合 | 分片内 | 跨分片合并 | 边界说明 |
|---|---|---|---|
| `count()` / `count(col)` | 行数 / col 非 None 行数 | 各分片计数相加 | 整数精确，与切法无关 |
| `sum(col)` | 非 None 值求和 | 部分和按分片序相加 | 整数精确；float 受结合律影响不保证按位一致（对拍数据均为整数） |
| `min` / `max(col)` | 非 None 值最值 | 各分片最值再取 min/max | 空结果为 None；结合/交换律安全 |
| `distinct(col)` | 非 None 值集合 | 集合取并集 | 输出 `sorted(并集)`（确定性），附 `n_distinct_col` 计数 |

None 一律跳过（SQL 风格）；`count()` 与 `count(col)` 的区别即是否计入 None 行。

## 行序保证

分片是输入行的**连续区间**（`shard_bounds`），worker 保持片内顺序，
归并按分片下标 0..k-1 拼接 —— 等价于串行一次扫完，逐行一致由
`diff_check.py` 的指纹（sha256 逐行序列化）和 17 个回归用例保证。

## 性能结论（100 万行，16 核，详见 results/bench_results.txt）

- 一次性池（每查询新建）：聚合 p=16 加速 1.8x；建池固定开销
  （fork 大内存进程的页表复制，1 GiB RSS 实测 0.16~0.28 s）会吞掉
  小查询收益，明细扫描（58 ms 级）此时不划算。
- 常驻池 `ParallelExecutor`（bind 一次 0.02~0.2 s）：聚合 p=16 加速
  **3.2x**，明细扫描 p=16 加速 **2.0x** —— 数据常驻、查询反复到来
  的真实引擎形态，推荐用法。
- workers=1 时只有 1 个分片，直接走串行基线，无进程开销。
