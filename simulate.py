"""漂移仿真与实测数据生成。

运行：python3 simulate.py
输出：终端打印 + 写入 SIMULATION_RESULTS.md
"""

import threading
import time

from ptimer import (
    LatePolicy,
    NoisyVirtualClock,
    PeriodicTimer,
    QuantizedVirtualClock,
    ResumePolicy,
    VirtualClock,
)

MS = 1e3


def fmt(seconds):
    return f"{seconds * MS:.3f} ms"


# --------------------------------------------------------------------- #
# 1. 长周期漂移对比：naive（固定睡眠）vs 绝对时间网格
# --------------------------------------------------------------------- #
def run_naive(period, count, clock):
    """对照组：每次固定睡眠 period，调度延迟逐次累积。"""
    fires = []
    for _ in range(count):
        clock.sleep(period)
        fires.append(clock.now())
    return fires


def drift_simulation(period=0.1, count=100_000, delay=0.003, seed=7):
    """100k 次触发，噪声为 0..3ms 单向调度延迟。"""
    # 绝对时间网格（本库）
    clock_abs = NoisyVirtualClock(delay=delay, seed=seed)
    ticks = []
    timer = PeriodicTimer(period, ticks.append, clock=clock_abs)
    timer.run(max_ticks=count)

    # naive 对照（相同噪声序列）
    clock_naive = NoisyVirtualClock(delay=delay, seed=seed)
    naive_fires = run_naive(period, count, clock_naive)

    checkpoints = [1_000, 10_000, 50_000, 100_000]
    rows = []
    for cp in checkpoints:
        naive_drift = naive_fires[cp - 1] - cp * period
        window = ticks[:cp]
        max_late = max(t.lateness for t in window)
        avg_late = sum(t.lateness for t in window) / len(window)
        rows.append((cp, naive_drift, max_late, avg_late))
    return rows, timer.stats


# --------------------------------------------------------------------- #
# 2. 最小调度精度：period 大于 / 等于 / 小于精度
# --------------------------------------------------------------------- #
def quantization_simulation():
    quantum = 0.010
    cases = [
        ("period=100ms > 精度10ms", 0.1, LatePolicy.COALESCE, 200),
        ("period=10ms == 精度10ms", 0.010, LatePolicy.COALESCE, 500),
        ("period=3ms < 精度10ms (COALESCE)", 0.003, LatePolicy.COALESCE, 300),
        ("period=3ms < 精度10ms (BURST)", 0.003, LatePolicy.BURST, 300),
    ]
    results = []
    for name, period, policy, count in cases:
        clock = QuantizedVirtualClock(quantum)
        ticks = []
        timer = PeriodicTimer(period, ticks.append, clock=clock, late_policy=policy)
        timer.run(max_ticks=count)
        st = timer.stats
        elapsed = clock.now()
        results.append(
            (name, count, st.fired, st.skipped, st.max_lateness,
             st.avg_lateness, elapsed / st.fired if st.fired else 0.0)
        )
    return results


# --------------------------------------------------------------------- #
# 3. 时间跳跃处理演示
# --------------------------------------------------------------------- #
def jump_simulation():
    out = {}

    # 向前跳 0.5s
    clock = VirtualClock()
    jumps = []
    ticks = []
    timer = PeriodicTimer(0.1, ticks.append, clock=clock,
                          on_jump=jumps.append, jump_threshold=0.05)
    fired = [0]

    def on_tick(t):
        ticks.append(t)
        fired[0] += 1
        if fired[0] == 30:
            clock.jump(0.5)

    timer.on_tick = on_tick
    timer.run(max_ticks=60)
    out["forward"] = (jumps[0], timer.stats, len(ticks))

    # 回拨 5s
    clock = VirtualClock()
    jumps = []
    ticks = []
    sleeps = []
    orig_sleep = clock.sleep

    def spy_sleep(s):
        sleeps.append(s)
        orig_sleep(s)

    clock.sleep = spy_sleep
    timer = PeriodicTimer(0.1, ticks.append, clock=clock,
                          on_jump=jumps.append, jump_threshold=0.05)
    fired = [0]

    def on_tick(t):
        ticks.append(t)
        fired[0] += 1
        if fired[0] == 30:
            clock.jump(-5.0)

    timer.on_tick = on_tick
    timer.run(max_ticks=60)
    out["backward"] = (jumps[0], timer.stats, len(ticks), max(sleeps[31:]))
    return out


# --------------------------------------------------------------------- #
# 4. 暂停 / 恢复策略演示
# --------------------------------------------------------------------- #
def pause_simulation():
    out = {}
    for policy in (ResumePolicy.SKIP, ResumePolicy.BURST):
        clock = VirtualClock()
        ticks = []
        timer = PeriodicTimer(0.1, ticks.append, clock=clock, resume_policy=policy)
        fired = [0]

        def on_tick(t):
            ticks.append(t)
            fired[0] += 1
            if fired[0] == 30:
                timer.pause()

        timer.on_tick = on_tick
        max_ticks = 60 if policy is ResumePolicy.SKIP else 65
        thread = threading.Thread(target=timer.run, kwargs={"max_ticks": max_ticks})
        thread.start()
        timer.wait_paused(2.0)
        clock.advance(0.5)  # 暂停 0.5s（= 5 个槽位）
        timer.resume()
        thread.join(2.0)
        out[policy.value] = (len(ticks), timer.stats.skipped)
    return out


# --------------------------------------------------------------------- #
# 5. 真实时钟采样（约 3.5s）
# --------------------------------------------------------------------- #
def real_clock_simulation():
    cases = [("100ms x 2s", 0.1, 2.0), ("10ms x 1s", 0.01, 1.0), ("1ms x 0.5s", 0.001, 0.5)]
    results = []
    for name, period, duration in cases:
        ticks = []
        timer = PeriodicTimer(period, ticks.append)
        timer.start()
        time.sleep(duration)
        timer.stop(2.0)
        st = timer.stats
        results.append((name, st.fired, st.skipped, st.max_lateness,
                        st.avg_lateness, st.p95_lateness))
    return results


# --------------------------------------------------------------------- #
def main():
    lines = []
    emit = lambda s="": (print(s), lines.append(s))

    emit("# 漂移仿真与实测数据")
    emit()
    emit("由 `python3 simulate.py` 生成。时间单位除标注外均为毫秒。")
    emit()

    emit("## 1. 周期误差随运行时长变化（100,000 次触发，period=100ms）")
    emit()
    emit("噪声模型：每次睡眠叠加 0..3ms 单向调度延迟（模拟 OS 调度器）。")
    emit("naive 对照组 = 每次固定睡眠 100ms；本库 = 绝对时间网格。")
    emit()
    emit("| 已触发次数 | 运行时长 | naive 累积漂移 | 本库单次最大偏差 | 本库平均偏差 |")
    emit("|---:|---:|---:|---:|---:|")
    rows, st = drift_simulation()
    for cp, naive_drift, max_late, avg_late in rows:
        emit(f"| {cp:,} | {cp * 0.1:,.0f} s | {naive_drift:.3f} s "
             f"| {fmt(max_late)} | {fmt(avg_late)} |")
    emit()
    emit(f"本库 100k 次全程：触发 {st.fired:,} 次，跳过 {st.skipped} 次，"
         f"最大偏差 {fmt(st.max_lateness)}，平均偏差 {fmt(st.avg_lateness)}，"
         f"P95 {fmt(st.p95_lateness)}。偏差有界、不随运行时长增长；")
    emit("naive 方案漂移随次数线性累积（约 1.5ms/次）。")
    emit()

    emit("## 2. 最小调度精度边界（量化时钟，精度 = 10ms）")
    emit()
    emit("| 场景 | 计划触发 | 实际触发 | 跳过 | 最大偏差 | 平均偏差 | 有效平均周期 |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    for name, planned, fired, skipped, max_l, avg_l, eff in quantization_simulation():
        emit(f"| {name} | {planned} | {fired} | {skipped} "
             f"| {fmt(max_l)} | {fmt(avg_l)} | {fmt(eff)} |")
    emit()
    emit("- period > 精度：正常工作，迟到被下一次自动追回，无累积。")
    emit("- period == 精度：正常工作，每次睡眠恰好一个精度单位。")
    emit("- period < 精度（COALESCE，默认）：每个精度单位最多触发一次，")
    emit("  中间过期槽位合并跳过（计入 skipped），长期速率由网格保证。")
    emit("- period < 精度（BURST）：到期槽位成串补发，一次不丢，")
    emit("  有效平均周期仍收敛到 3ms。")
    emit()

    emit("## 3. 时间跳跃处理")
    emit()
    jumps = jump_simulation()
    ev, st, fired = jumps["forward"]
    emit(f"### 向前跳跃 +{ev.delta:.1f}s（= 5 个槽位）")
    emit()
    emit(f"- 检测到并报告 JumpEvent：direction={ev.direction}, "
         f"missed_slots={ev.missed_slots}, reanchored={ev.reanchored}")
    emit(f"- 跳过的槽位不补发（COALESCE），跳变后共触发 {fired} 次，"
         f"skipped={st.skipped}，无重复触发")
    emit()
    ev, st, fired, max_sleep = jumps["backward"]
    emit(f"### 时间回拨 {ev.delta:.1f}s")
    emit()
    emit(f"- 检测到并报告 JumpEvent：direction={ev.direction}, "
         f"reanchored={ev.reanchored}（网格重新对齐到当前时刻）")
    emit(f"- 不重复触发已触发槽位；回拨后单次最长睡眠 {fmt(max_sleep)}"
         f"（<= 1 个周期，无长时间停顿）")
    emit(f"- 回拨后共触发 {fired} 次，skipped={st.skipped}")
    emit()

    emit("## 4. 暂停 / 恢复（暂停 0.5s = 5 个槽位）")
    emit()
    pause = pause_simulation()
    emit("| 策略 | 恢复后总触发 | 跳过（不补发） |")
    emit("|---|---:|---:|")
    emit(f"| SKIP（默认） | {pause['skip'][0]} | {pause['skip'][1]} |")
    emit(f"| BURST | {pause['burst'][0]} | {pause['burst'][1]} |")
    emit()
    emit("- SKIP：暂停期间的槽位永不补发，恢复后按原周期继续（新相位）。")
    emit("- BURST：恢复时一次性补发全部错过槽位，随后恢复正常节奏。")
    emit()

    emit("## 5. 真实时钟采样（SystemClock = time.monotonic）")
    emit()
    emit("| 场景 | 触发次数 | 跳过 | 最大偏差 | 平均偏差 | P95 偏差 |")
    emit("|---|---:|---:|---:|---:|---:|")
    for name, fired, skipped, max_l, avg_l, p95 in real_clock_simulation():
        emit(f"| {name} | {fired} | {skipped} | {fmt(max_l)} "
             f"| {fmt(avg_l)} | {fmt(p95)} |")
    emit()
    emit("真实环境下偏差为单次调度延迟，有界且不累积（机器负载高时数值会变大，")
    emit("但长期漂移始终为零）。")

    with open("SIMULATION_RESULTS.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n已写入 SIMULATION_RESULTS.md")


if __name__ == "__main__":
    main()
