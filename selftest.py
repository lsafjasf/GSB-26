"""合成数据自测：已知偏移/漂移下验证估计准确度、合并不变量、缺失段检测。

运行：python3 selftest.py
"""
import math
import random

from timeline_merge import (
    Event, Source, merge, check_invariants,
    estimate_offset_from_events, estimate_offset_xcorr,
)

T_SPAN = 60000            # 参考时间跨度
B_OFFSET = 350            # B 的真实固定偏移
B_DRIFT = 2e-5            # B 的真实相对漂移
C_OFFSET = -800           # C 的真实固定偏移（无漂移）
B_DROP = (20000, 26000)   # B 人为制造的缺失时段（参考时间）
QUIET = (40000, 45000)    # 公共事件静默期（无事件 ≠ 缺失）
BUCKET_WIDTH = 50


def build_sources():
    rng = random.Random(42)

    # 公共事件（带 key），非周期间隔，静默期内不安排
    key_times = []
    t = 350
    k = 0
    while t < T_SPAN:
        if not (QUIET[0] <= t < QUIET[1]):
            key_times.append((t, f"ev{k}"))
            k += 1
        t += 500 + rng.randint(0, 400)

    def to_b(t_ref, jitter=0):
        return round(t_ref * (1 + B_DRIFT) + B_OFFSET) + jitter

    def to_c(t_ref, jitter=0):
        return round(t_ref + C_OFFSET) + jitter

    # A：参考来源，连续采样，间隔 10
    a_events = [Event(t, payload="tick") for t in range(0, T_SPAN + 1, 10)]
    for t_ref, key in key_times:
        a_events.append(Event(t_ref, payload="event", key=key))

    # B：连续采样，间隔 7（与 A 采样率不同），偏移+漂移+抖动，含缺失时段
    b_events = []
    for t_ref in range(0, T_SPAN + 1, 7):
        if B_DROP[0] <= t_ref < B_DROP[1]:
            continue  # 模拟数据丢失
        b_events.append(Event(to_b(t_ref, rng.randint(-1, 1)), payload="tick"))
    for t_ref, key in key_times:
        if B_DROP[0] <= t_ref < B_DROP[1]:
            continue
        b_events.append(Event(to_b(t_ref, rng.randint(-2, 2)),
                              payload="event", key=key))

    # C：事件驱动（无连续采样），仅上报部分公共事件 + 随机噪声事件
    c_events = []
    for i, (t_ref, key) in enumerate(key_times):
        if i % 2 == 0:
            c_events.append(Event(to_c(t_ref, rng.randint(-3, 3)),
                                  payload="event", key=key))
    for _ in range(200):
        c_events.append(Event(rng.randint(C_OFFSET, T_SPAN + C_OFFSET),
                              payload="noise"))
    return a_events, b_events, c_events


def main():
    a_events, b_events, c_events = build_sources()
    n_in = {"A": len(a_events), "B": len(b_events), "C": len(c_events)}
    print(f"输入事件数: A={n_in['A']} B={n_in['B']} C={n_in['C']}")

    # ---- 1. 偏移/漂移估计（公共事件法） ----
    clock_b, n_pairs_b = estimate_offset_from_events(a_events, b_events)
    clock_c, n_pairs_c = estimate_offset_from_events(a_events, c_events)
    print("\n== 偏移估计（公共事件 + 最小二乘） ==")
    print(f"B: offset={clock_b.offset:+.3f} ± {clock_b.offset_uncertainty:.3f} "
          f"(真值 {B_OFFSET}), drift={clock_b.drift:+.3e} ± "
          f"{clock_b.drift_uncertainty:.1e} (真值 {B_DRIFT}), 匹配 {n_pairs_b} 对")
    print(f"C: offset={clock_c.offset:+.3f} ± {clock_c.offset_uncertainty:.3f} "
          f"(真值 {C_OFFSET}), drift={clock_c.drift:+.3e} (真值 0), "
          f"匹配 {n_pairs_c} 对")
    assert abs(clock_b.offset - B_OFFSET) < 5, "B 偏移估计超差"
    assert abs(clock_b.drift - B_DRIFT) < 2e-5, "B 漂移估计超差"
    assert abs(clock_c.offset - C_OFFSET) < 5, "C 偏移估计超差"
    assert abs(clock_c.drift) < 5e-5, "C 漂移应接近 0"

    # ---- 2. 偏移估计（互相关法，不使用 key） ----
    a_evt_times = [e.t for e in a_events if e.key is not None]
    b_evt_times = [e.t for e in b_events if e.key is not None]
    clock_bx, info = estimate_offset_xcorr(a_evt_times, b_evt_times,
                                           fine_bin=1, bootstrap=25, seed=1)
    print("\n== 偏移估计（互相关 + bootstrap 不确定度） ==")
    print(f"B: offset={clock_bx.offset:+.2f} ± {clock_bx.offset_uncertainty:.2f} "
          f"(真值约 {B_OFFSET}，含漂移项)")
    assert abs(clock_bx.offset - B_OFFSET) < 10, "互相关偏移估计超差"

    # ---- 3. 合并 ----
    sources = [
        Source("A", a_events, nominal_interval=10, continuous=True),
        Source("B", b_events, clock=clock_b, nominal_interval=7,
               continuous=True),
        Source("C", c_events, clock=clock_c, continuous=False),
    ]
    merged = merge(sources, bucket_width=BUCKET_WIDTH, gap_factor=3.0,
                   reference="A")

    # 排序与分桶规则自检
    for r_prev, r_next in zip(merged.records, merged.records[1:]):
        assert r_prev.aligned_time <= r_next.aligned_time, "结果未按对齐时间排序"
    for r in merged.records:
        assert r.bucket == math.floor(r.aligned_time / BUCKET_WIDTH),             "分桶归属规则不一致"

    # ---- 4. 合并不变量 ----
    check_invariants(merged, sources)
    print(f"\n== 合并不变量 == 通过：每来源出现次数 == 输入次数，"
          f"总记录 {len(merged.records)} 条")

    # ---- 5. 缺失段 vs 无事件 ----
    print("\n== 缺失段检测 ==")
    for seg in merged.missing_segments:
        print(f"来源 {seg.source}: 参考时间 [{seg.start_ref:.0f}, "
              f"{seg.end_ref:.0f}], 约缺 {seg.expected_samples} 个样本")
    b_segs = [s for s in merged.missing_segments if s.source == "B"]
    assert len(b_segs) == 1, "B 应恰好检出 1 个缺失段"
    assert abs(b_segs[0].start_ref - B_DROP[0]) < 15
    assert abs(b_segs[0].end_ref - B_DROP[1]) < 15
    assert not any(s.source == "A" for s in merged.missing_segments),         "A 无缺失"
    assert not any(s.source == "C" for s in merged.missing_segments),         "C 为事件驱动，静默期不应判为缺失"
    print("通过：B 的缺失段被检出；C 的静默期记为「无事件」而非缺失")

    # ---- 6. 对齐质量指标 ----
    print("\n== 对齐质量（公共事件残差，参考时间单位） ==")
    for name, stats in merged.quality.items():
        print(f"{name}: n={stats['n']} median={stats['median']:+.3f} "
              f"MAD={stats['mad']:.3f} p95|r|={stats['p95_abs']:.3f} "
              f"max|r|={stats['max_abs']:.3f} RMS={stats['rms']:.3f}")
        assert stats["p95_abs"] < 10, f"{name} 对齐残差过大"

    print("\n全部断言通过 ✔")


if __name__ == "__main__":
    main()
