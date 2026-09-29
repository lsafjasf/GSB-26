# flakyhunter — 不稳定测试识别框架

纯 Python 3 标准库实现，零第三方依赖。针对「同一份代码同一批测试偶尔失败偶尔通过」
的问题，用重复执行 + 统计判定取代「反复重跑 / 直接忽略」。

## 目录结构

```
flakyhunter/            框架源码
  core.py               测试发现、执行、记录（结果/耗时/种子/并行度/顺序）
  judge.py              稳定性判定（阈值与置信度）
  order_analysis.py     固定 vs 打乱顺序对比
  quarantine.py         隔离清单（可审计）
  cli.py                命令行入口
selftest/
  injected_tests.py     注入测试：20 稳定 + 1 稳定失败 + 4 已知失败率 flaky
  order_tests.py        注入的顺序相关测试（共享状态污染）
  run_selftest.py       框架自测（误判/检出/顺序/隔离四项检查）
experiments/            实验数据（JSONL 原始记录 + 报告）
```

## 运行命令

```bash
# 1. 重复执行测试集（50 轮 x 固定+打乱两种模式），记录结果
python3 -m flakyhunter run --tests selftest/injected_tests.py \
    --repeats 50 --mode both --jobs 1 --out experiments/injected.jsonl

# 2. 稳定性判定（发现 flaky 时退出码为 1，可直接接 CI）
python3 -m flakyhunter judge --results experiments/injected.jsonl

# 3. 顺序相关性对比
python3 -m flakyhunter order --results experiments/order.jsonl

# 4. 隔离管理（必须填写操作人与原因，保证可审计）
python3 -m flakyhunter quarantine add --id test_flaky_p050 \
    --by zhangsan --reason "p=0.5 随机失败，待修复" --review-after 2026-10-01
python3 -m flakyhunter quarantine list   # 查看隔离清单
python3 -m flakyhunter quarantine due    # 到期需复查的隔离测试

# 5. 分「普通集 / 隔离集」汇报（隔离测试仍被执行，只是单独汇报）
python3 -m flakyhunter report --results experiments/injected.jsonl

# 6. 框架自测
python3 selftest/run_selftest.py
```

## 判定阈值说明

对某个测试重复执行 `n` 次，失败（含 error）`k` 次：

| 条件 | 判定 | 说明 |
|---|---|---|
| `k = 0` | `stable_pass` | 全部通过 |
| `k = n` | `stable_fail` | 全部失败（是真 bug，不是不稳定） |
| `0 < k < n` | `flaky` | 同一测试出现两种结果，直接判定不稳定 |

**置信度（针对 stable 判定）**：设 `p0` 为「最低值得关心的失败率」（默认 0.05，
可用 `--p0` 调整）。两类稳定结论按不同方向分别推导，不共用公式：

- `stable_pass`（按失败率推导）：若真实失败率 ≥ p0，则 n 次全部通过的概率
  只有 `(1-p0)^n`，因此「失败率 < p0」的置信度为：

  ```
  confidence(stable_pass) = 1 - (1 - p0)^n
  ```

- `stable_fail`（按通过率推导）：若真实通过率 ≥ 1-p0（即失败率 ≤ p0），
  则 n 次全部失败的概率只有 `p0^n`，因此「失败率 > p0」的置信度为：

  ```
  confidence(stable_fail) = 1 - p0^n
  ```

| 重复次数 n | p0=0.05 时置信度 |
|---|---|
| 20 | 64.2% |
| 50 | 92.3% |
| 100 | 99.4% |

（上表为 `stable_pass`；`stable_fail` 收敛更快，n=20 时误差已小于 1e-26。）
即：想以 95% 置信度排除 ≥5% 的失败率，至少需要约 59 次全通过的执行。
对 `flaky` 判定，报告失败率的 **Wilson 95% 置信区间**（小样本下比正态近似更准）。

**顺序相关判定**：同一测试在固定顺序与打乱顺序下失败率之差 > 20%，
且至少一侧失败 ≥ 2 次，判定为「顺序相关」（典型根因：测试间共享状态污染）。

## 实验数据

### 判定结果（experiments/injected.jsonl，50 轮 x 2 模式 = 每测试 100 次）

| 测试 | 注入失败率 | 观测失败率 (95% CI) | 判定 |
|---|---|---|---|
| test_stable_00..19 | 0 | 0.000 [0.000, 0.037] | stable_pass（置信度 99.4%） |
| test_stable_fail | 1.0 | 1.000 [0.963, 1.000] | stable_fail（置信度 1.0000，误差 0.05^100≈7.9e-131） |
| test_flaky_p010 | 0.10 | 0.100 [0.055, 0.174] | flaky |
| test_flaky_p030 | 0.30 | 0.290 [0.210, 0.385] | flaky |
| test_flaky_p050 | 0.50 | 0.460 [0.366, 0.557] | flaky |
| test_flaky_p080 | 0.80 | 0.780 [0.689, 0.850] | flaky |

观测失败率均落在注入真值附近，Wilson 区间均覆盖真值。

### 顺序相关性（experiments/order.jsonl，每模式 50 轮）

| 测试 | 固定顺序失败 | 打乱顺序失败 | 顺序相关 |
|---|---|---|---|
| test_order_a_polluter（污染源） | 0/50 | 0/50 | no |
| test_order_b_victim（受害者） | **50/50** | 24/50 | **YES** |
| test_order_c_independent | 0/50 | 0/50 | no |

固定顺序下 polluter 总在 victim 之前执行，victim 100% 失败；打乱后约一半轮次
victim 排在 polluter 之前，失败率降至 ~50%。差异被框架自动标记为顺序相关。

### 框架自测（selftest/run_selftest.py）

```
== 1. 误判检查 ==   20 个稳定测试 x 100 次执行 = 2000 条记录，误判为 flaky: 0 (误判率 0.0%)
== 2. 检出检查 ==   4 个注入 flaky 全部检出；稳定失败正确判为 stable_fail
== 2b. 置信度 ==   stable_pass=1-(1-p0)^n=0.9941 与 stable_fail=1-p0^n=1.0000 分别推导，断言不共用公式
== 3. 顺序相关性 == victim 固定 50/50 vs 打乱 26/50，被标记为顺序相关；独立测试不误标
== 4. 隔离清单 ==   审计字段完整；到期复查生效；隔离测试仍被执行；缺操作人/原因时拒绝隔离
自测全部通过
```

**误判数据说明**：`stable_pass`/`stable_fail` 的判定规则是「n 次结果完全一致」，
确定性测试不存在被误判为 flaky 的路径，2000 次执行实测误判 0 次。
唯一理论上的误判来源是环境噪声（如机器过载导致超时），这正是需要记录
环境信息（种子/并行度/顺序/平台）以便回溯的原因。

**检出能力边界**：失败率为 p 的 flaky 测试在 n 次执行中「恰好全通过」被漏判的
概率为 `(1-p)^n`。n=100 时：p=0.10 漏判率 0.003%，p=0.05 漏判率 0.6%，
p=0.01 漏判率 36.6% —— 低频 flaky 需要更多重复次数，可用 `--repeats` 与
`--p0` 按团队可接受的漏判率换算所需规模。

## 设计要点

- **可复现**：每轮种子写入 `FLAKY_SEED` 环境变量并记入 JSONL，注入测试的
  随机性完全由种子驱动，任何一次失败都可用同一种子重放。
- **隔离 ≠ 跳过**：隔离测试照常执行，`report` 单独成节汇报；隔离记录包含
  操作人 / 原因 / 隔离时间 / 复查日期，`quarantine due` 列出到期项。
- **CI 集成**：`judge` 发现 flaky 时退出码为 1（`--allow-flaky` 可关闭），
  可直接接入流水线门禁。
