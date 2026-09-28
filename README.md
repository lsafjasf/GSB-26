# logengine：结构化日志查询引擎

纯 Python 3 标准库实现，零依赖。日志按时间分块存储，查询时把过滤条件下推到块级，
利用块内统计（时间边界、字段分布）跳过无关块，并支持按时间倒序取前 N 条提前终止。

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 对拍测试（33 个用例）
python3 bench.py                           # 扫描量与耗时基准
```

## 快速上手

```python
from logengine import Store, query

store = Store()
for rec in records:              # records 必须按 ts 升序
    store.append(rec)            # rec 是 dict，必须含数值字段 ts
    if 凑满一个块:
        store.seal_block()       # 封存时自动计算块统计
store.flush()

res = query(store, "SELECT * WHERE service = 'api' AND ts >= 1700000000 "
                   "ORDER BY ts DESC LIMIT 10")
res.data                 # 命中记录 / 计数 / 分组结果
res.metrics              # scanned_blocks / pruned_blocks / skipped_limit_blocks / scanned_records
```

## 查询语法

类 SQL 子集，关键字大小写不敏感，字段名大小写敏感：

```
query       := SELECT select_list WHERE conditions
               [ GROUP BY 字段 ] [ ORDER BY ts (ASC|DESC) ] [ LIMIT 非负整数 ] [ ; ]
select_list := * | COUNT(*)
conditions  := condition ( AND condition )*          -- 仅支持 AND 合取
condition   := 字段 ( = | != | < | <= | > | >= ) 值
值          := 整数 | 浮点数 | '字符串' | "字符串" | true | false | null
```

示例：

```sql
SELECT * WHERE ts >= 1700000000 AND ts < 1700003600 AND service = 'api'
SELECT * WHERE level = 'error' AND latency >= 200 ORDER BY ts DESC LIMIT 20
SELECT COUNT(*) WHERE ts >= 1700000000
SELECT COUNT(*) WHERE ts >= 1700000000 AND env = 'prod' GROUP BY service
```

语义约定：

- `ts` 为内置时间字段（数值，秒级时间戳），每条记录必须存在；`ORDER BY` 仅支持 `ts`。
- 缺失字段：任何比较均为假（`!=` 也不例外）。
- 跨类型比较（如字符串与数值）恒为假，不抛异常。
- `GROUP BY` 需配合 `SELECT COUNT(*)`，输出按分组键排序的 `(键, 计数)` 列表，保证确定性。
- `SELECT *` 默认按块封存顺序（即 ts 升序）输出；`ORDER BY ts DESC` 为倒序。
  排序键为 `(ts, 写入序号)`：并列时间戳时 DESC 取后写入者在前，ASC 取先写入者在前，
  提前终止与全量扫描两条执行路径共用这一全序。

## 块级下推设计

每个块封存时计算统计（`logengine/storage.py`）：

- 时间边界 `min_ts` / `max_ts`；
- 每字段的精确取值集合（`FieldStats.values`）与数值 `min_val` / `max_val`。

规划器（`logengine/planner.py`）对合取条件逐条尝试证伪，任一条件证伪即跳过整块：

| 条件 | 下推规则 |
|---|---|
| `ts </<=/>/>=/= v` | 与块时间边界求交，区间为空则跳过（严格/非严格边界均处理） |
| `field = v` | `v` 不在块内该字段取值集合中则跳过 |
| `field != v` | 块内该字段取值集合恰为 `{v}` 则跳过 |
| `field </<=/>/>= v`（数值） | 与块内 `[min_val, max_val]` 求交，为空则跳过 |
| 块内无该字段 | 除 `!=` 保守保留外，其余跳过 |

取值集合对高基数字段会占内存，生产实现可替换为布隆过滤器（接口不变，仍只能证伪不能证实）。

执行器（`logengine/engine.py`）：

- 普通查询：按块顺序扫描候选块，块内逐条评估全部条件。
- `ORDER BY ts DESC LIMIT N`：候选块按 `max_ts` 降序逐块扫描，凑满 N 条立即终止，
  剩余候选块记为 `skipped_limit_blocks`，不读一条记录。
  正确性前提：块间时间区间不重叠且单调递增（`Store.append` 强制校验）。
- 指标不变量：`total_blocks = scanned_blocks + pruned_blocks + skipped_limit_blocks`。

## 对拍测试

`tests/test_engine.py`，两条执行路径对拍：`execute`（下推 + 提前终止）vs
`full_scan`（禁用一切优化、扫描全部块的参考实现），结果必须完全一致：

- 确定性用例：单块、跨块、全覆盖、边界时间（含/不含端点）、空结果、缺失字段、
  跨类型比较、`!=`、布尔值、COUNT/GROUP BY、LIMIT 0/超量/ASC/DESC 等，
  并对扫描块数做精确断言；
- 随机对拍：固定种子生成 8 块 × 50 条数据集，400 条随机查询（等值/范围/聚合/TopN 混合）
  逐一比对结果与指标不变量；另有单块存储的 100 条随机查询对拍。

## 扫描量与耗时实测

`python3 bench.py`：200,000 条记录、20 个块 × 10,000 条，每查询取 5 次最优耗时，
全部结果已与全量扫描对拍一致（Python 3.12，本机实测）：

| 场景 | 扫描块 | 跳过块(下推/限流) | 扫描记录 | 命中 | 引擎耗时ms | 全扫耗时ms | 加速比 |
|---|---|---|---|---|---|---|---|
| 时间范围·单块 | 1/20 | 19/0 | 10000 | 1112 | 5.00 | 54.14 | 10.8x |
| 时间范围·跨5块 | 5/20 | 15/0 | 50000 | 45000 | 15.84 | 61.85 | 3.9x |
| 时间范围·覆盖全部 | 20/20 | 0/0 | 200000 | 200000 | 39.71 | 39.89 | 1.0x |
| 等值·命中率极低(1/20万) | 1/20 | 19/0 | 10000 | 1 | 1.51 | 31.20 | 20.7x |
| 等值·命中率极高(100%) | 20/20 | 0/0 | 200000 | 200000 | 37.38 | 35.56 | 1.0x |
| 等值·无匹配 | 0/20 | 20/0 | 0 | 0 | 0.00 | 32.86 | >8000x |
| 数值范围·跨2块 | 2/20 | 18/0 | 20000 | 10000 | 6.12 | 62.05 | 10.1x |
| 分组计数·全量 | 20/20 | 0/0 | 200000 | 200000 | 49.08 | 51.25 | 1.0x |
| Top10·倒序提前终止 | 1/20 | 0/19 | 10000 | 10 | 0.01 | 51.65 | >6000x |
| Top5·带过滤提前终止 | 1/20 | 16/3 | 10000 | 5 | 0.01 | 35.21 | >6000x |
| 空结果·时间在未来 | 0/20 | 20/0 | 0 | 0 | 0.01 | 37.92 | >5000x |

要点：下推命中时扫描量与耗时随候选块数线性下降；全覆盖/100% 命中时引擎开销与
全扫持平（约 1.0x，无额外成本）；TopN 只扫最新 1 个块即终止。

## 文件结构

```
logengine/
  __init__.py   # 包入口与公开 API
  storage.py    # Store / Block / 块统计构建
  parser.py     # 查询语法解析（手写递归下降）
  planner.py    # 块级条件下推（跳块）
  engine.py     # 执行器、提前终止、指标、full_scan 参考实现
tests/
  test_engine.py  # 对拍测试
bench.py          # 扫描量与耗时基准
```
