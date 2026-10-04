# qengine — 分片并行查询引擎（纯标准库 Python 3）

在串行扫描基线之上，用 `concurrent.futures.ProcessPoolExecutor` 按数据分片
并行扫描，父进程合流。**并行只改变执行方式，不改变结果**：任意并行度的
输出都与串行基线逐行逐值一致（对拍自动化）。

## 目录与交付物

| 文件 | 内容 |
|---|---|
| `qengine/expr.py` | 行表达式（嵌套 tuple，可 pickle）：算术/比较/三值逻辑/IN/NULL 判定 |
| `qengine/plan.py` | 查询计划 `Plan` / `AggSpec` / `OrderKey` |
| `qengine/table.py` | 类型化 CSV 分片读写、分片切分 |
| `qengine/engine.py` | 串行/并行执行入口、聚合状态、合流、排序、top-k 下推、对拍断言 |
| `qengine/queries.py` | golden/基准共用的查询集（串行与并行用同一份 Plan） |
| `gen_golden.py` | 生成对拍输入分片与串行基准结果 |
| `verify_golden.py` | 对拍：各并行度结果 vs golden（逐行逐值） |
| `test_qengine.py` | 19 个边界用例（小数据/倾斜/单分片/跨分片聚合/NULL/排序…） |
| `gen_bench.py` | 基准数据生成（默认 100 万行 / 16 分片，确定性 LCG） |
| `bench.py` | 多并行度耗时对比（含正确性闸门，输出 `bench_results.txt`） |
| `data/golden/` | 对拍数据：`input/shard-*.csv` + `expected/*.csv` |
| `bench_results.txt` | 最近一次基准原始数据 |

## 运行方式

```sh
# 1) 边界用例
python3 test_qengine.py -v

# 2) 对拍（需要先有 golden 数据；脚本幂等可重复生成）
python3 gen_golden.py
python3 verify_golden.py

# 3) 并行度耗时基准
python3 gen_bench.py                  # 默认 1000000 行 / 16 分片
python3 bench.py                      # workers=1,2,4,8,16，每档 3 次取中位数
python3 bench.py --workers 1 2 4 --repeats 5   # 自定义
```

或 `bash run_all.sh` 一次跑完以上三步。要求 Python 3.10+（开发于 3.12），
无任何第三方依赖。

## 并行模型与"顺序不变"的保证

- 输入是**有序分片列表**（文件名排序即分片顺序）。行的全局身份是
  `(分片号, 片内序号)` 拼成的全局行号 `idx`。
- 每个 worker 独立读自己的分片、过滤、投影/局部聚合，返回带分片号的结果；
  父进程按分片号收齐（与 worker 完成先后无关）。
- **明细查询**：每行带 `idx`，合流后按 `idx` 稳定排序归位，因此输出顺序与
  "全表单遍扫描"完全相同；`ORDER BY` 的并列由稳定排序天然落到 `idx`
  （即扫描序）兜底，串行/任意并行度的并列顺序一致。
- **聚合查询**：每分片产出部分聚合状态，父进程按组键合流；组键含 `None`
  时 `None` 自成一组。无显式 `ORDER BY` 时按组键确定性排序
  （`None` 视为最小），输出顺序同样与完成顺序无关。
- `ORDER BY + LIMIT` 做**本地 top-k 下推**：全局 top-k 必含于各分片
  top-k 并集（同排序键、并列走 `idx`、稳定排序），每片只回传 `limit` 行；
  排序键是复杂表达式时保守放弃下推（有测试覆盖两条路径）。

## 聚合口径（分片边界语义）

设分片各自先做局部聚合，再做跨分片合流：

| 聚合 | 局部状态 | 合流运算 | NULL 口径 | 跨分片正确性依据 |
|---|---|---|---|---|
| `count(*)` | 行数 | 相加 | 计全部行 | 计数可加 |
| `count(expr)` | 非 NULL 行数 | 相加 | 表达式为 NULL 不计 | 非空计数可加 |
| `sum(expr)` | 非 NULL 值之和 | 相加 | NULL 跳过；无输入为 0 | 加法结合/交换（整型逐位一致；float 见下注） |
| `min/max(expr)` | 非 NULL 最值 | 再取最值 | NULL 跳过；无输入为 NULL | 最值运算结合/交换，**逐位一致** |
| `avg(expr)` | (sum, count) | sum 相加、count 相加，最后 `sum/count` | NULL 跳过；无输入为 NULL | 不能对各片平均值再平均；本引擎合流的是**原始分子分母** |
| `count_distinct(expr)` | 本地去重集合 | 集合求并后取长度 | NULL 不入集合 | 去重的并集 = 全集去重，**逐位一致** |
| `collect_distinct(expr)` | 本地去重集合 | 集合并集后排序输出 | NULL 不入集合 | 同上；排序保证输出确定 |

`GROUP BY k` 与无 `GROUP BY` 的标量聚合都走同一套合流；标量聚合在
**0 行命中时仍返回一行**（`count=0, sum=0, min/max/avg=NULL`），与 SQL 一致。

**float 的 `sum/avg` 是唯一非结合性口径**：浮点加法结合顺序不同会有末位
差异（`(a+b)+c ≠ a+(b+c)`），因此整型/计数/最值/去重做**严格逐值**对拍，
浮点求和用 `math.isclose`（默认 rel/abs 容差 1e-12）对拍。生产中若要求
浮点也逐位一致，应改用整数定点数（如金额用"分"），基准数据集的金额列即
为整数。

## 对拍数据（data/golden/）

52 行 / 4 分片（**12、3、0（空分片）、37 行**，刻意倾斜），确定性 LCG
+ 手工边界行：
- 热点用户 u1 约占 40%；region/status/amount/score 均含 NULL
- amount 区间 [-10, 30]，含 0、负数、NULL
- score 存在跨分片同分值（检验排序并列兜底）
- 6 个查询覆盖：过滤投影、跨分片排序+limit、五种口径的 region 聚合、
  用户倾斜聚合、空结果标量聚合、NULL 分组键

`verify_golden.py` 对每个查询 × 并行度 {1,2,3,4,8}（含并行度>分片数）
比对 golden 与现场串行结果；`test_qengine.py` 19 个用例全部要求
`assert_rows_equal` 通过。

## 实测耗时（1,000,000 行 / 16 分片，16 核机器）

中位数秒（3 次重复，含进程池启动、CSV 解析、扫描、合流）：

| 查询 | w=1 | w=2 | w=4 | w=8 | w=16 |
|---|---|---|---|---|---|
| agg_region（全口径） | 2.26 | 1.30 | 0.81 | 0.59 (3.8x) | 0.56 (4.0x) |
| agg_user_skew（~1000 组，热点倾斜） | 2.30 | 1.32 | 0.85 | 0.64 (3.6x) | 0.65 (3.5x) |
| select_filter（~1% 命中，明细保序） | 1.63 | 0.98 | 0.61 | 0.44 (3.7x) | 0.46 (3.5x) |
| order_top50（top-k 下推） | 3.01 | 1.69 | 1.01 | 0.76 (4.0x) | 0.75 (4.0x) |

观察：w=8 起加速比饱和（16 个分片、父进程串行合流、进程启动/IPC 成本，
超线程核共享资源），这是真实端到端口径；增大行数/减少结果集体积时
曲线会更陡。机器不同数值会变，请以本机 `python3 bench.py` 输出为准，
原始数据见 `bench_results.txt`。

## 编程接口

```python
from qengine import Schema, run, split_evenly
from qengine import expr as E
from qengine.plan import Plan, OrderKey, count_star, sum_

schema = Schema([("id", "int"), ("amt", "int")])
rows = [{"id": i, "amt": i * 3} for i in range(1000)]
shards = split_evenly(rows, 4)                      # 内存分片；也可用 CSV 路径
plan = Plan(
    filter=E.gt(E.col("amt"), E.lit(100)),
    select=(("id", E.col("id")), ("amt", E.col("amt"))),
    order_by=(OrderKey(E.col("amt"), desc=True),),
    limit=10,
)
print(run(shards, schema, plan, workers=4))         # workers=1 即串行基线
```
