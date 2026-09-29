# GSB-26 服务降级与健康检查状态机

纯 Python 3 标准库实现，时钟由调用方注入（测试用 `FakeClock`，生产传 `time.time`）。

## 文件

- `health_fsm.py` — 库：健康等级、组件状态机、组合判定、功能门控、审计日志
- `test_health_fsm.py` — 自测（12 个用例，覆盖全部正常 / 间歇失败 / 全部失败 / 快速震荡）
- `demo.py` — 演示脚本，打印四种场景的状态序列与成功率时间线、审计记录

## 运行命令

```bash
python3 -m unittest test_health_fsm -v   # 运行自测
python3 demo.py                          # 打印时间线与审计样例
```

## 健康等级定义

| 等级 | 数值 | 判定条件 | 允许的功能 | 禁止的功能 |
|---|---|---|---|---|
| 完整 FULL | 3 | 全部组件健康 | 全部 | — |
| 部分降级 PARTIAL | 2 | 关键组件全部健康，存在非关键组件异常 | core_query、write_order | recommend、cache_warmup |
| 最小可用 MINIMAL | 1 | 至少一个关键组件异常，但仍有关键组件健康 | core_query（只读简化流程） | write_order、recommend、cache_warmup |
| 不可用 DOWN | 0 | 全部关键组件异常 | — | 全部 |

示例部署：`db`、`payment` 为关键组件；`cache`、`recommend` 为非关键组件。
功能门控见 `FEATURE_MIN_LEVEL`，通过 `monitor.is_allowed(feature)` 查询。

## 平滑（迟滞）规则

- 单次失败不降级：组件**连续失败 ≥ 3 次**（`fail_threshold`）才判定为异常。
- 恢复要求**连续成功 ≥ 2 次**（`recover_threshold`）才判定为健康；中途一次失败即清零重计。
- 成功率使用滑动窗口（默认 20 次）计算，仅用于审计与观测，不参与判定。
- 失败阈值 > 恢复阈值形成迟滞带，快速震荡时等级不会随每次检查抖动。

## 多组件组合规则

设 `critical_down` 为异常的关键组件集合，`unhealthy` 为全部异常组件集合：

1. 所有关键组件均异常 → DOWN（取最严重，整体不可用）
2. 否则存在关键组件异常 → MINIMAL
3. 否则存在非关键组件异常 → PARTIAL
4. 否则 → FULL

即整体等级 = 各组件异常影响的最大值，关键组件异常优先于非关键组件异常。

注意：只有在配置了至少一个关键组件（`total_critical > 0`）时才可能判为 DOWN；
否则“异常关键组件数 == 关键组件总数”会是 `0 == 0` 恒真。未配置关键组件时按非关键
组件集合计算：全部健康为 FULL，部分异常为 PARTIAL，全部异常为 MINIMAL（不会出现 DOWN）。

## 审计记录

每次等级变化追加一条 `AuditRecord`：时间戳、旧/新等级、依据组件（降级归因当前异常组件，恢复归因刚恢复的组件）、当时各组件滑动窗口成功率。样例（场景3）：

```json
{"timestamp": 3.0,  "old_level": "完整",   "new_level": "最小可用", "cause_components": ["db"],            "success_rates": {"db": 0.0, "payment": 1.0, "cache": 1.0, "recommend": 1.0}}
{"timestamp": 6.0,  "old_level": "最小可用", "new_level": "不可用",  "cause_components": ["db", "payment"], "success_rates": {"db": 0.0, "payment": 0.0, "cache": 1.0, "recommend": 1.0}}
{"timestamp": 14.0, "old_level": "不可用",  "new_level": "最小可用", "cause_components": ["db"],            "success_rates": {"db": 0.4, "payment": 0.0, "cache": 0.0, "recommend": 0.0}}
{"timestamp": 16.0, "old_level": "最小可用", "new_level": "部分降级", "cause_components": ["payment"],       "success_rates": {"db": 0.4, "payment": 0.4, "cache": 0.0, "recommend": 0.0}}
{"timestamp": 20.0, "old_level": "部分降级", "new_level": "完整",   "cause_components": ["recommend"],     "success_rates": {"db": 0.4, "payment": 0.4, "cache": 0.4, "recommend": 0.4}}
```

## 状态序列与成功率时间线（`python3 demo.py` 输出摘要）

场景2 单组件（cache，非关键）间歇失败 → 持续失败 → 恢复：

```
 t  check       level     cache成功率
 3  cache:FAIL  完整      0.67     <- 第 1 次失败，不降级
 4  cache:FAIL  完整      0.50     <- 第 2 次失败，不降级
 5  cache:ok    完整      0.60     <- 成功清零连续失败计数
 9  cache:FAIL  部分降级  0.44     <- 连续 3 次失败，触发降级
12  cache:ok    部分降级  0.42     <- 第 1 次成功，未达恢复阈值
13  cache:FAIL  部分降级  0.38     <- 失败清零连续成功计数
15  cache:ok    完整      0.47     <- 连续 2 次成功，恢复
等级序列: 完整 → 部分降级 → 完整（审计 2 条）
```

场景3 全部失败 → 恢复：

```
等级序列: 完整 → 最小可用(t=3, db 异常) → 不可用(t=6, db+payment 异常)
        → 最小可用(t=14, db 恢复) → 部分降级(t=16, payment 恢复) → 完整(t=20)
```

场景4 快速震荡（cache 3 连败 / 2 连胜 × 3 轮）：

```
等级序列: 完整 ⇄ 部分降级 共 6 次切换，每轮恰好 2 次（降级+恢复），
最终回到完整；若失败不足阈值（2 连败后成功）则完全不降级（见自测）。
```

场景1 全部正常：等级始终为完整，无审计记录。
