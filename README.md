# ptimer — 无累积漂移的周期性计时器库

纯 Python 3 标准库实现，时间源可注入。每次触发时刻由**绝对时间网格**
`origin + n*period` 决定，睡眠时长始终为「下一个网格点 − 当前时刻」，
单次调度延迟会被下一次自动追回，**长期运行零漂移**。

## 运行命令

```bash
python3 test_timer.py     # 17 个单元测试（含边界用例，约 2s）
python3 simulate.py       # 漂移仿真 + 跳跃/暂停/精度降级演示，生成 SIMULATION_RESULTS.md
```

## 快速上手

```python
from ptimer import PeriodicTimer

def on_tick(tick):
    print(tick.index, tick.scheduled, tick.actual, tick.lateness)

timer = PeriodicTimer(0.1, on_tick)   # 默认使用 time.monotonic
timer.start()                          # 守护线程运行
...
timer.pause(); timer.resume(); timer.stop()
```

注入虚拟时钟可确定性运行（测试 / 仿真）：

```python
from ptimer import PeriodicTimer, VirtualClock

clock = VirtualClock()
timer = PeriodicTimer(0.1, print, clock=clock)
timer.run(max_ticks=1000)              # 瞬时跑完 1000 个周期
```

## 核心设计

| 机制 | 说明 |
|---|---|
| 绝对时间网格 | 第 n 次触发的计划时刻为 `origin + n*period`，与历史触发无关 |
| 追回式睡眠 | 每次睡眠 `scheduled(n) - now`，迟到自动少睡，误差不累积 |
| 可注入时钟 | `Clock` 协议（`now()` / `sleep()`），内置 `SystemClock`、`VirtualClock`、`QuantizedVirtualClock`、`NoisyVirtualClock` |
| 单调时钟 | 真实模式用 `time.monotonic`，不受墙钟（NTP 校时）回拨影响 |

## 时间跳跃处理

跳跃检测：每次醒来后比较实测流逝与计划，偏差超过 `jump_threshold`
（默认 `max(period, 0.25s)`，可调）判定为跳跃，通过 `on_jump` 回调
报告 `JumpEvent`（方向、跳跃量、错过槽位数、是否重锚）。

- **向前跳跃**：保持绝对网格不变，越过的槽位按 `late_policy` 处理
  （默认 COALESCE：合并跳过并计数；BURST：成串补发）。不重复、不停顿。
- **时间回拨**：网格重新对齐到当前时刻，下一槽位 = `now + period`。
  已触发的槽位序号单调递增，**绝不重复触发**；最多多等一个周期，
  **不会长时间停顿**。小于阈值的小幅回拨不触发重锚，停顿同样有界（≤ 2 个周期）。

## 暂停 / 恢复

`pause()` 阻塞到循环确认暂停；`resume()` 恢复。`resume_policy` 决定
暂停期间错过槽位的处理：

- `SKIP`（默认）：**不补偿**被跳过的次数，统计进 `stats.skipped`，
  恢复后按原周期继续（同频新相位；若暂停窗口未跨越槽位则保持原相位）。
- `BURST`：恢复时一次性**补发**全部错过槽位，随后恢复正常节奏。

## 最小调度精度降级行为

以 10ms 精度为例（实测数据见 SIMULATION_RESULTS.md 第 2 节）：

- `period > 精度`：正常工作，迟到被下一次追回，无累积。
- `period == 精度`：正常工作，每次睡眠恰好一个精度单位。
- `period < 精度`：无法按原频率触发，两种降级策略——
  COALESCE（默认）每个精度单位最多触发一次、中间槽位合并跳过；
  BURST 到期槽位成串补发、一次不丢，有效平均周期仍收敛到 period。

## 边界用例（test_timer.py）

- 理想时钟 1000 次触发零漂移；抖动时钟 10k 次偏差有界
- 精度量化三种情形（大于 / 等于 / 小于）
- 向前跳跃跳过并报告；BURST 补发不丢
- 回拨 5s 不重复、不停顿；小幅回拨（< 阈值）不重锚、停顿有界
- 暂停/恢复两种策略；暂停窗口未跨槽位时保持相位
- 真实时钟冒烟：触发、暂停期间零触发、恢复继续
- 默认时钟忙等回归：等待期间 CPU 时间远低于墙钟时间，触发精度不退化

## 实测数据摘要

完整数据见 [SIMULATION_RESULTS.md](SIMULATION_RESULTS.md)（`python3 simulate.py` 生成）。

100,000 次触发、period=100ms、每次睡眠叠加 0..3ms 调度延迟：

| 已触发次数 | naive（固定睡眠）累积漂移 | 本库单次最大偏差 | 本库平均偏差 |
|---:|---:|---:|---:|
| 1,000 | 1.505 s | 2.993 ms | 1.505 ms |
| 100,000 | 149.895 s | 3.000 ms | 1.499 ms |

naive 方案漂移随次数线性增长；本库偏差始终有界（≤ 最大单次调度延迟）。
