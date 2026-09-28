# 时序指标降采样库

纯 Python 3 标准库实现，时间戳一律用整数表示。把细粒度采样点聚合到更大的展示窗口，同时保证聚合口径正确、空洞可见、结果与到达顺序无关。

## 文件

- `downsample.py` — 库源码（`downsample` 分桶实现 + `naive_downsample` 逐点朴素参照实现）
- `test_downsample.py` — 自测（口径、空洞、边界、乱序、重复、对拍）
- `bench.py` — 百万点性能基准

## 聚合口径

聚合器分两类，**同一次请求混用两类会抛 `MixedAggregationError`**：

| 类别 | 聚合器 | 说明 |
|---|---|---|
| 可加 `ADDITIVE` | `count`, `sum` | 计数、求和类指标 |
| 不可加 `NON_ADDITIVE` | `max`, `avg`, `p50`/`p90`/`p95`/`p99`, `quantile(p)` | 最大值、平均值、分位数 |

把求和类指标按平均处理（或反之）会让曲线失真，因此库在 API 层面强制口径一致；自定义聚合器通过 `register_aggregator(Aggregator(name, kind, func))` 注册，必须声明类别。

## 窗口与边界规则

- 窗口按 epoch 对齐的**半开区间** `[k*window, (k+1)*window)`，窗口下标 `k = t // window`（floor 划分，负时间戳同样适用）。
- 恰好落在边界上的点属于**右侧**窗口（如 `t=60, window=60` 落入 `[60,120)`）。
- 输出范围默认取数据本身覆盖的窗口；也可用 `start=`/`end=` 显式指定 `[start, end)`，超出数据范围的窗口输出为空洞。

## 空洞、乱序、重复

- **空洞**：窗口内无采样点时 `Bucket.values is None`（`bucket.empty == True`），绝不静默补零。
- **乱序**：内部按窗口下标一趟分桶，与输入顺序无关；同一批数据任意打乱后结果完全相同（有测试保证）。`sum`/`avg` 采用 `math.fsum` 精确求和（正确舍入），同一多重集合任意累加顺序结果逐比特一致——即使窗口内混有 1e16 与 0.1 这样的大动态范围数据，也不会像逐项累加那样因顺序不同产生不同浮点结果（`test_large_dynamic_range_order_independent` 用 400 种打乱顺序断言结果唯一且等于数学精确和）。
- **重复时间戳**：视为独立采样点，全部计入聚合。

## 正确性保证

`naive_downsample` 是逐点朴素参照实现：每个窗口独立全量扫描所有点。`test_downsample.py` 中的对拍测试在 30 组随机种子 × 多种窗口（1/7/60/3600）× 两类口径下，逐窗口断言两种实现的聚合值一致；并覆盖单点、单窗口、跨上百窗口、时间范围超出数据范围、空输入等情形。

## 运行命令

```bash
python3 -m unittest test_downsample -v   # 跑全部自测（24 个用例）
python3 bench.py                          # 百万点性能基准
```

## 用法示例

```python
from downsample import downsample

points = [(0, 1.0), (10, 2.0), (59, 3.0), (60, 4.0), (180, 5.0)]
for b in downsample(points, 60, ["count", "sum"], start=0, end=240):
    print(b.start, "空洞" if b.empty else b.values)
# 0 {'count': 3.0, 'sum': 6.0}
# 60 {'count': 1.0, 'sum': 4.0}
# 120 空洞
# 180 {'count': 1.0, 'sum': 5.0}
```

## 性能数据

环境：Python 3.12，100 万随机点（含乱序、重复时间戳），窗口 60s，共 16667 个窗口：

| 操作 | 耗时 |
|---|---|
| `downsample` 可加口径（count+sum） | ~275 ms |
| `downsample` 不可加口径（max+avg+p50+p95） | ~363 ms |
| `naive_downsample` 参照（仅 2 万点，O(n×窗口数)） | ~4.5 s |

分桶实现为 O(n + 窗口数)，百万点亚秒级；朴素实现随窗口数线性放大，仅用于对拍。基准同时验证百万点乱序后结果逐窗口相等。
