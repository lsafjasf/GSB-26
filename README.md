# 业务判定代码重构：嵌套条件 → 规则表

## 文件

| 文件 | 说明 |
|---|---|
| `legacy_decision.py` | 重构前的嵌套条件实现，作为差分测试基准（冻结，不再修改） |
| `rule_engine.py` | 通用规则引擎：优先级匹配 + 加载时校验（冲突/不可达/默认规则） |
| `refactored_decision.py` | 重构后的业务规则表（15 条规则，纯数据，零分支） |
| `datasets.py` | 对拍/分析共用的数据集：边界网格（6912 例）+ 确定性模糊（20000 例） |
| `rule_coverage.py` | 规则覆盖度分析：命中统计、死规则检测、未覆盖分支、删除对拍 |
| `analyze_coverage.py` | 覆盖度分析入口，生成 `coverage_report.txt` |
| `test_decision.py` | 差分测试、顺序无关性测试、冲突/不可达检测用例 |
| `test_coverage.py` | 覆盖度分析回归测试（命中数核算/死规则/删除对拍/报告可复算） |

## 运行命令

```bash
python3 -m unittest test_decision test_coverage -v   # 全部 30 个测试
python3 analyze_coverage.py --check                  # 覆盖度分析，报告写入 coverage_report.txt
```

## 规则表设计

每条规则显式声明三要素：匹配条件、判定结果、优先级。引擎按优先级从高到低
匹配，全部未命中时走**显式声明的默认规则**（`conditions=None, priority=0`）。
判定行为只由优先级决定，与声明顺序无关——`OrderIndependenceTest` 将规则表
随机打乱 5 次并整体逆序，逐例验证结果不变。

## 覆盖清单（原分支 → 规则 → 测试）

原实现 19 个 if/elif 分支点、6 种结果，与规则表一一对应：

| 原嵌套分支 | 规则序号（优先级） | 结果 | 覆盖测试 |
|---|---|---|---|
| flagged ∧ tier∈{gold,platinum} ∧ days≥365 | 规则 1 (100) | manual_review | 网格含 days=364/365/366 边界 |
| flagged ∧ 其余 | 规则 2 (90) | reject | 全 tier × flagged=True |
| overseas ∧ amount≥10000 | 规则 3 (80) | manual_review | amount=9999/10000/10001 |
| overseas 其余 | 规则 4 (70) | standard | 网格全组合 |
| remote ∧ amount≥5000 | 规则 5 (60) | manual_review | amount=4999/5000/5001 |
| remote 其余 | 规则 6 (50) | standard | 网格全组合 |
| platinum ∧ amount≥2000 | 规则 7 (45) | vip_fast | amount=1999/2000/2001 |
| platinum 其余 | 规则 8 (44) | discount_20 | 网格全组合 |
| gold ∧ coupon ∧ amount≥1000 | 规则 9 (43) | discount_20 | amount=999/1000/1001 |
| gold ∧ amount≥3000 | 规则 10 (42) | vip_fast | amount=2999/3000/3001 |
| gold 其余 | 规则 11 (41) | discount_10 | 网格全组合 |
| silver ∧ coupon ∧ amount≥500 | 规则 12 (40) | discount_10 | amount=499/500/501 |
| silver 其余 | 规则 13 (39) | standard | 网格全组合 |
| normal ∧ coupon ∧ amount≥200 | 规则 14 (38) | discount_10 | amount=199/200/201 |
| 默认分支（最后的 return "standard"） | 规则 15 (0) | standard | 网格全组合 |

差分测试（`DifferentialTest`）：
- 全组合网格 4 tier × 3 region × 24 金额边界值 × 6 天数边界值 × 2 用券 × 2 风控
  = **6912 例**，重构前后逐例一致；
- 20000 例随机模糊（连续/整数金额混合）逐例一致；
- 断言 6 种结果（含默认分支）均被网格实际触达。

## 冲突与不可达检测（加载时）

`Engine` 构造时校验，失败抛 `RuleError` 并指出规则序号（按声明位置从 1 编号）：

- **冲突**：两规则条件重叠 + 优先级相同 + 结果不同
  （`test_conflict_same_priority_overlap`；另有用例证明"优先级不同"或
  "条件不重叠"时不误报）；
- **不可达**：条件自相矛盾（如 `amount≥1000 且 <500`），或被某条更高优先级
  规则完全覆盖（`test_unreachable_*`）；
- **默认规则**：缺失或不唯一均报错（`test_missing/duplicate_default_rule`）。

## 规则覆盖度分析（rule_coverage.py）

规则表越写越多，需要知道哪些规则从来没生效。`analyze(rules, cases)` 输出：

- **命中统计**：每条规则在数据集上"决定最终判定"的次数与占比；
- **死规则**：条件自相矛盾，或被若干条更高优先级规则的**并集**完全遮蔽
  （比引擎加载时的单条覆盖校验更强，能发现"多条规则合力遮蔽"）；
- **未覆盖分支清单**：数据集上命中 0 次的规则（不一定是死规则，
  删除前需人工确认）；
- **删除对拍**：删除死规则后，原表（免校验匹配器）与清理后 `Engine`
  在同一数据集上逐例比对，证明行为不变。

可复算性：分析无随机源、无时间戳，报告文本完全由（规则表, 数据集, 取值域）
决定；`ReportReproducibilityTest` 验证同一输入逐字节一致、与用例顺序无关。
集合型字段的取值域由 `infer_domains` 从数据集观测推断；缺省时对
"无约束字段的并集遮蔽"保守处理为不可判定，**不会误报死规则**。

当前业务规则表（15 条）在 6912 例网格 + 20000 例模糊上的结论
（`coverage_report.txt`，`python3 analyze_coverage.py --check` 可复跑）：
15 条规则全部命中、无死规则、默认规则可达、删除对拍逐例一致。

## 新增一种判定结果要改哪里

**唯一位置：`refactored_decision.py` 的 `RULES` 列表追加一条 `Rule`**
（条件 + 结果 + 优先级）。引擎、校验、测试基础设施零改动；加载时自动获得
冲突/不可达检查，差分测试自动覆盖新规则。

## 重构前后对比

| 指标 | 重构前 `legacy_decision.py` | 重构后 |
|---|---|---|
| 业务代码行数 | 88 行 | 57 行（`refactored_decision.py`，纯数据） |
| 业务判定分支数（if/elif 节点） | 19 | **0** |
| 最大嵌套深度 | 7 层 | 0 层 |
| 新增情形的改动面 | 多处嵌套插入，易破坏既有分支 | 规则表追加 1 行 |
| 顺序敏感性的表达 | 隐式（依赖 if/else 书写顺序） | 显式（priority 字段） |

通用引擎 `rule_engine.py`（224 行，38 个 if/elif）为一次性基础设施，
不随规则数量增长；业务侧分支数从 19 降为 0。
