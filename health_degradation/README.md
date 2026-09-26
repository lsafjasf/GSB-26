# 服务降级与健康检查状态机

纯 Python 3 标准库实现，无第三方依赖。时间通过 `clock` 参数注入，测试与演示使用假时钟，生产默认 `time.monotonic`。

## 文件

- `health_degradation.py` — 库（等级、组件状态机、组合规则、审计日志）
- `test_health_degradation.py` — 自测（unittest，12 个用例）
- `demo.py` — 场景演示，打印成功率与等级时间线、审计记录

## 运行命令

```bash
cd health_degradation
python3 test_health_degradation.py   # 运行自测
python3 demo.py                      # 运行场景演示
```

## 健康等级定义

| 等级 | 含义 | 允许的功能 | 禁止的功能 |
|---|---|---|---|
| `FULL` (3) | 完整 | 全部：个性化推荐、全文检索、实时写入、缓存读取、静态内容、健康端点 | — |
| `PARTIAL` (2) | 部分降级 | 全文检索、实时写入、缓存读取、静态内容、健康端点 | 个性化推荐（裁剪增强功能，保主流程） |
| `MINIMAL` (1) | 最小可用 | 缓存读取、静态内容、健康端点 | 推荐、检索、写入 |
| `UNAVAILABLE` (0) | 不可用 | 仅健康端点（便于外部探活/摘流量） | 其余全部 |

等级数值越大能力越完整，允许集随等级严格单调收敛（有测试保证）。

## 判定与平滑规则

每个组件是独立的 up/down 状态机，带迟滞（hysteresis）：

- **降级**：连续失败达到 `fail_threshold`（默认 3）才判定组件下线。单次失败、成败交替不会改变状态。
- **恢复**：连续成功达到 `recover_threshold`（默认 2）才判定组件恢复。恢复阈值低于失败阈值，保证恢复比降级更快，同时仍能吸收抖动。
- 成功率统计使用滑动窗口（默认最近 20 次，演示用 10 次），仅用于审计与观测，不直接驱动状态翻转——状态翻转只看连续计数，因此快速震荡不会产生等级抖动（场景 4 验证）。

## 多组件组合规则

组件分两类：

- **critical（硬门槛）**：任一 critical 组件下线，整体直接 `UNAVAILABLE`，无论其他组件多健康。
- **非 critical（加权计分）**：计算健康权重占比 `score = 健康组件权重和 / 全部非 critical 权重和`，按下表映射：

| score | 等级 |
|---|---|
| = 1.0 | `FULL` |
| ≥ 0.6 | `PARTIAL` |
| ≥ 0.3 | `MINIMAL` |
| < 0.3 | `UNAVAILABLE` |

示例部署：`db`(critical) + `cache`(0.40) + `search`(0.35) + `recommend`(0.25)：

- recommend 下线 → score 0.75 → `PARTIAL`
- recommend + search 下线 → score 0.40 → `MINIMAL`
- 三者全下线 → score 0.0 → `UNAVAILABLE`
- db 下线（其他全健康）→ `UNAVAILABLE`（硬门槛优先）

## 审计记录

整体等级每次变化追加一条 `AuditRecord`：

```python
AuditRecord(
    timestamp=1000090.0,               # 等级变化时间（注入时钟）
    old_level=FULL, new_level=PARTIAL, # 状态序列
    trigger_component='recommend',     # 依据的检查项
    trigger_ok=False,
    down_components=('recommend',),    # 变化后下线组件集合
    success_rate=0.33,                 # 当时滑动窗口整体成功率
    component_rates=(('cache', 1.0), ('db', 1.0), ('recommend', 0.33), ('search', 1.0)),
)
```

## 状态序列与成功率时间线（`python3 demo.py` 实测输出）

### 场景 2：单组件间歇失败（recommend）

```
      t(s) component  ok       rate  level
   1000010 recommend  False    0.00  FULL      <- 单次失败，不降级
   1000020 recommend  True     0.50  FULL
   1000030 recommend  False    0.33  FULL
   1000040 recommend  True     0.50  FULL
   1000050 recommend  False    0.40  FULL
   1000060 recommend  True     0.50  FULL      <- 间歇失败始终 FULL
   1000070 recommend  False    0.43  FULL      <- 连续失败 1
   1000080 recommend  False    0.38  FULL      <- 连续失败 2，仍不降级
   1000090 recommend  False    0.33  PARTIAL   <- 连续失败 3，达到阈值，降级
   1000100 recommend  True     0.40  PARTIAL   <- 连续成功 1，不恢复
   1000110 recommend  True     0.50  FULL      <- 连续成功 2，恢复
```

审计：`t=1000090 FULL->PARTIAL (rate=0.33)`，`t=1000110 PARTIAL->FULL (rate=0.50)`。

### 场景 3：全部失败与恢复

```
   1000010 db         False    0.00  FULL
   1000020 db         False    0.00  FULL
   1000030 db         False    0.00  UNAVAILABLE   <- critical 组件达阈值，硬门槛生效
   ...    cache/search/recommend 各 3 连败 ...      UNAVAILABLE（维持）
   1000130 cache      True     0.10  UNAVAILABLE
   ...    非 critical 组件全部恢复 ...              UNAVAILABLE（db 仍下线，硬门槛）
   1000190 db         True     0.70  UNAVAILABLE   <- db 连续成功 1
   1000200 db         True     0.80  FULL          <- db 连续成功 2，整体恢复
```

### 场景 4：快速震荡（search 成败交替 16 次）

成功率在 0.33–0.50 间波动，但任何方向都凑不齐连续阈值，等级始终 `FULL`，审计日志为空——零抖动、零噪音。

### 场景 1：全部正常

等级始终 `FULL`，成功率 1.00，无审计记录。

## 自测覆盖

| 测试类 | 覆盖点 |
|---|---|
| `TestAllHealthy` | 全部正常，等级稳定，功能全允许 |
| `TestIntermittentFailure` | 单次/两次失败不降级；3 连败降级；1 次成功不恢复；2 连成功恢复；审计内容 |
| `TestTotalFailure` | 全部失败 → `UNAVAILABLE`；critical 硬门槛；恢复序列 |
| `TestRapidFlapping` | 成败交替、阈值边缘震荡均被吸收，零审计噪音 |
| `TestComposition` | 多组件同时异常的加权组合；critical 覆盖权重；各组件成功率快照 |
| `TestCapabilitiesMatrix` | 等级 allow/deny 不重叠且单调收敛 |
