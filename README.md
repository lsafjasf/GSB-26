# 业务判定代码重构：嵌套条件 → 规则表

场景：物流履约方式与运费判定。输入 `(level, amount, weight, region, fragile)`，
输出 `(履约方式, 运费)`。仅使用 Python 3 标准库。

## 文件

| 文件 | 说明 |
|---|---|
| `legacy.py` | 原始实现（嵌套条件，重构前基准，保持不动） |
| `rule_engine.py` | 规则表引擎：显式优先级匹配 + 加载期冲突/不可达校验 |
| `rules.py` | **规则表：业务规则的唯一声明位置** |
| `refactored.py` | 重构后入口：组装上下文、调引擎、解析结果，无业务分支 |
| `tests/test_differential.py` | 差分回归：2160 例穷举比对 + 规则覆盖断言 |
| `tests/test_ordering.py` | 顺序敏感性：乱序等价验证 + 优先级交换行为验证 |
| `tests/test_conflicts.py` | 冲突 / 不可达 / 默认分支校验用例 |
| `tests/test_trace.py` | 来源追踪：2160 例追踪-判定一致性 + 追踪内容 + 报告导出 |
| `demo_trace.py` | 追踪演示：打印代表性用例的追踪并导出 `trace_report.md` |

## 运行命令

```bash
cd A
python3 -m unittest discover -s tests -v   # 全部测试（18 个）
python3 -m unittest tests.test_differential -v   # 仅差分回归
python3 -m unittest tests.test_ordering -v       # 仅顺序敏感性
python3 -m unittest tests.test_conflicts -v      # 仅冲突检测
python3 -m unittest tests.test_trace -v          # 仅来源追踪
python3 demo_trace.py                            # 打印追踪并导出 trace_report.md
```

## 来源追踪（为什么是这个结果）

- `Engine.explain(ctx)` 与 `match` 使用**同一份规则表**（同一 `_ordered`
  与 `_OPS`），返回 `Trace`：首命中规则、每条未命中规则的首个不满足
  条件（含实际值）、条件同样满足但因优先级被跳过的规则
  （`trace.skipped_by_priority`）、默认分支生效原因。
- `refactored.explain(...)` 返回 `(判定结果, Trace)`；
  `Trace.render()` 输出单条可读追踪；`rule_engine.render_report /
  export_report` 把多次追踪渲染 / 导出为一份 Markdown 报告。
- 一致性可断言：`tests/test_trace.py` 对 2160 例穷举输入逐例断言
  `trace.rule is engine.match(ctx)` 且 `explain` 结果 == `decide` 结果。

## 设计要点

- **规则表**：每条规则声明 `rule_id / priority / conditions / result`；
  引擎按 `priority` 降序匹配，首个命中者生效。
- **默认分支显式声明**：`default=True` 的规则必须恰好一条，缺失或重复
  都会在加载期报 `RuleTableError`。
- **顺序敏感逻辑显式编码**：匹配顺序只由 `priority` 决定，与声明顺序无关。
  重叠规则对（R05>R06、R08>R09、R01/R02>其它非海外规则）的先后关系
  全部体现在优先级数值上。`test_ordering.py` 验证：随机打乱声明顺序
  行为不变；交换重叠规则的优先级，行为按预期改变。
- **加载期校验**（`rule_engine.validate`，报错均指出规则编号）：
  - `CONFLICT A <-> B`：条件重叠、优先级相同、结果不同；
  - `UNREACHABLE X`：条件自相矛盾，或被更高/相等优先级规则完全遮蔽；
  - `DEFAULT`：默认规则缺失或不唯一。

## 新增一种判定结果要改哪里

**只改 `rules.py` 的 `RULES` 列表一处**：追加一条 `Rule`（声明条件、
结果、优先级）即可。引擎、入口、差分测试框架均无需改动；若新规则与
现有规则冲突或被遮蔽，加载期校验会直接报错并指出规则编号。

## 覆盖清单（原分支 → 规则 → 边界用例）

| 原实现分支 | 规则 | 关键边界用例（amount / weight） |
|---|---|---|
| 海外 vip 轻量 → express 0 | R03 | weight = 20 / 20.0001 |
| 海外 vip 超重 → freight 200 | R04 | weight = 20.0001 |
| 海外非 vip 超重 → reject | R05 | weight = 30 / 30.0001（且 amount ≥ 1000 验证优先级） |
| 海外非 vip 大额 → express 50 | R06 | amount = 999.99 / 1000 |
| 海外非 vip 其它 → standard 120 | R07 | amount = 0 |
| 非海外易碎超重 → freight 80+2w | R01 | weight = 10 / 10.0001 |
| 非海外易碎轻量 → express 30 | R02 | weight = 10 |
| 偏远 vip 大额 → express 0 | R08 | amount = 499.99 / 500（且 weight > 15 验证优先级） |
| 偏远超重 → freight 100 | R09 | weight = 15 / 15.0001 |
| 偏远其它 → standard 40 | R10 | — |
| 本地 vip 大额 → drone 0 | R11 | amount = 299.99 / 300 |
| 本地 vip 其它 → express 0 | R12 | amount = 0 |
| 本地 member 大额 → express 0 | R13 | amount = 199.99 / 200 |
| 本地 member 其它 → standard 10 | R14 | — |
| 本地 guest 大额 → express 20 | R15 | amount = 499.99 / 500 |
| 本地 guest 超重 → standard 15 | R16 | weight = 5 / 5.0001 |
| 默认分支 → standard 8 | DEFAULT | guest / local / 非易碎 / amount<500 / weight≤5 |

`test_differential.py` 穷举 3等级 × 3区域 × 2易碎 × 10金额 × 12重量
= **2160 例**，逐例断言重构前后结果一致，并断言上表每条规则
（含 DEFAULT）至少命中一次。

## 重构前后对比

| 指标 | 重构前 `legacy.py` | 重构后 |
|---|---|---|
| 行数 | 74 | `refactored.py` 25 + `rules.py` 84（引擎 235 行为可复用基础设施） |
| 业务分支数（if/elif/else） | 36 | `refactored.py` 1（callable 解析）、`rules.py` 0（纯数据） |
| 返回点 | 19 处 `return` | 1 处 |
| 新增判定的改动位置 | 多处嵌套条件 | 仅 `rules.py` 一处 |
| 顺序敏感约定 | 隐式（依赖 if 书写顺序） | 显式（`priority` 数值） |
| 冲突 / 不可达检测 | 无 | 加载期自动校验并指出规则编号 |
