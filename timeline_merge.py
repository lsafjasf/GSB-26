"""多来源时间线对齐与合并库（仅标准库，时间为整数）。

核心概念：
- 每个来源有自己的本地时钟，与参考时钟存在固定偏移 offset 与相对漂移 drift：
      t_ref = (t_src - offset) / (1 + drift)
- 偏移估计两种方式：
  1) 公共事件匹配（事件带 key）：最小二乘拟合 offset/drift，并给出不确定度；
  2) 互相关（无需 key）：对事件时间直方图做粗-精两级互相关，bootstrap 估计不确定度。
- 合并：所有事件换算到参考时间后排序；分桶归属规则为
      bucket = floor(t_aligned / bucket_width)
  只做归属标注，不做重采样丢弃，任何来源的数据都不会丢失。
- 缺失段：连续采样来源（continuous=True）相邻事件间隔超过
  gap_factor * nominal_interval 判定为「缺失」；事件驱动来源
  （continuous=False）的长间隔只表示「该时段无事件」，不判缺失。
"""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from statistics import median


# ---------------------------------------------------------------- 数据模型

@dataclass
class Event:
    t: int                 # 来源本地整数时间
    payload: object = None
    key: object = None     # 公共事件标识（可选），用于跨来源匹配


@dataclass
class ClockModel:
    """来源时钟 -> 参考时钟的线性模型。"""
    offset: float = 0.0
    drift: float = 0.0
    offset_uncertainty: float = 0.0   # 1-sigma
    drift_uncertainty: float = 0.0    # 1-sigma

    def to_reference(self, t: float) -> float:
        return (t - self.offset) / (1.0 + self.drift)

    def to_source(self, t: float) -> float:
        return t * (1.0 + self.drift) + self.offset


@dataclass
class Source:
    name: str
    events: list                       # list[Event]
    clock: ClockModel = field(default_factory=ClockModel)
    nominal_interval: float = None     # 标称采样间隔；None 时由中位数间隔估计
    continuous: bool = True            # 连续采样(True) / 事件驱动(False)


@dataclass
class MergedRecord:
    aligned_time: float
    bucket: int
    source: str
    event: Event


@dataclass
class MissingSegment:
    source: str
    start_ref: float
    end_ref: float
    expected_samples: int              # 按标称间隔估算缺失的样本数


@dataclass
class MergedTimeline:
    records: list                      # list[MergedRecord]，按 aligned_time 排序
    bucket_width: int
    missing_segments: list             # list[MissingSegment]
    quality: dict                      # {source: 残差统计 dict}


# ---------------------------------------------------------------- 偏移估计

def match_by_key(ref_events, src_events):
    """按 key 匹配公共事件，返回 [(t_src, t_ref), ...]。"""
    ref_by_key = {}
    for e in ref_events:
        if e.key is not None and e.key not in ref_by_key:
            ref_by_key[e.key] = e.t
    pairs = []
    for e in src_events:
        if e.key is not None and e.key in ref_by_key:
            pairs.append((e.t, ref_by_key[e.key]))
    pairs.sort()
    return pairs


def fit_clock_model(pairs):
    """由匹配对 (t_src, t_ref) 最小二乘拟合 t_ref = a*t_src + b，
    换算为 ClockModel 并传播不确定度（忽略 a/b 协方差的线性近似）。"""
    n = len(pairs)
    if n < 2:
        raise ValueError("至少需要 2 对匹配事件")
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    denom = n * sxx - sx * sx
    if denom == 0:
        raise ValueError("匹配事件时间无变化，无法拟合")
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    if n > 2:
        s2 = sum((a * p[0] + b - p[1]) ** 2 for p in pairs) / (n - 2)
    else:
        s2 = 0.0
    s = math.sqrt(s2)
    se_a = s / math.sqrt(denom / n) if denom else 0.0
    se_b = s * math.sqrt(sxx / denom) if denom else 0.0
    drift = 1.0 / a - 1.0
    offset = -b / a
    drift_unc = se_a / (a * a)
    offset_unc = abs(se_b / a) + abs(b * se_a / (a * a))
    return ClockModel(offset=offset, drift=drift,
                      offset_uncertainty=offset_unc,
                      drift_uncertainty=drift_unc)


def estimate_offset_from_events(ref_events, src_events):
    """公共事件法：返回 (ClockModel, 匹配对数)。"""
    pairs = match_by_key(ref_events, src_events)
    return fit_clock_model(pairs), len(pairs)


def _xcorr_best_lag(ref_times, src_times, bin_width, lag_lo, lag_hi):
    bins_ref = Counter(t // bin_width for t in ref_times)
    bins_src = Counter(t // bin_width for t in src_times)
    best_lag, best_val = lag_lo, -1.0
    for lag in range(lag_lo, lag_hi + 1):
        v = 0
        for k, av in bins_ref.items():
            bv = bins_src.get(k + lag)
            if bv:
                v += av * bv
        if v > best_val:
            best_val, best_lag = v, lag
    return best_lag, best_val


def _xcorr_offset(ref_times, src_times, fine_bin):
    """粗-精两级互相关，返回 src 相对 ref 的偏移（src_time - ref_time）。"""
    span = max(ref_times[-1], src_times[-1]) - min(ref_times[0], src_times[0])
    coarse_bin = max(fine_bin, span // 500, 1)
    lag_lim = span // coarse_bin + 2
    coarse_lag, _ = _xcorr_best_lag(ref_times, src_times, coarse_bin,
                                    -lag_lim, lag_lim)
    center = coarse_lag * coarse_bin
    half = 3 * coarse_bin
    lo, hi = center - half, center + half
    fine_lo, fine_hi = lo // fine_bin, hi // fine_bin + 1
    lag, _ = _xcorr_best_lag(ref_times, src_times, fine_bin, fine_lo, fine_hi)
    # 峰值邻域加权质心细化
    bins_ref = Counter(t // fine_bin for t in ref_times)
    bins_src = Counter(t // fine_bin for t in src_times)
    num, den = 0.0, 0.0
    for d in (-1, 0, 1):
        v = sum(av * bins_src.get(k + lag + d, 0)
                for k, av in bins_ref.items())
        if v > 0:
            num += (lag + d) * v
            den += v
    return (num / den) * fine_bin if den else lag * fine_bin


def estimate_offset_xcorr(ref_times, src_times, fine_bin=1,
                          bootstrap=25, seed=0):
    """互相关法估计固定偏移，返回 (ClockModel, 峰值信息 dict)。
    不确定度由 bootstrap 重采样的标准差给出。"""
    ref = sorted(ref_times)
    src = sorted(src_times)
    if len(ref) < 2 or len(src) < 2:
        raise ValueError("需要至少 2 个事件")
    offset = _xcorr_offset(ref, src, fine_bin)
    rng = random.Random(seed)
    estimates = []
    for _ in range(bootstrap):
        r = sorted(ref[rng.randrange(len(ref))] for _ in range(len(ref)))
        s = sorted(src[rng.randrange(len(src))] for _ in range(len(src)))
        estimates.append(_xcorr_offset(r, s, fine_bin))
    mean = sum(estimates) / len(estimates)
    var = sum((e - mean) ** 2 for e in estimates) / max(1, len(estimates) - 1)
    model = ClockModel(offset=offset, drift=0.0,
                       offset_uncertainty=math.sqrt(var))
    info = {"bootstrap_std": math.sqrt(var), "bootstrap_n": bootstrap}
    return model, info


# ---------------------------------------------------------------- 质量指标

def residual_stats(residuals):
    """残差分布摘要：median / MAD / p95(|r|) / max(|r|) / RMS。"""
    n = len(residuals)
    if n == 0:
        return {"n": 0}
    med = median(residuals)
    mad = median([abs(r - med) for r in residuals])
    abs_sorted = sorted(abs(r) for r in residuals)
    p95 = abs_sorted[min(n - 1, int(0.95 * (n - 1)))]
    rms = math.sqrt(sum(r * r for r in residuals) / n)
    return {"n": n, "median": med, "mad": mad, "p95_abs": p95,
            "max_abs": abs_sorted[-1], "rms": rms}


def alignment_quality(ref_events, src_events, src_clock,
                      ref_clock=None):
    """对齐后公共事件残差：aligned(t_src) - aligned(t_ref)。"""
    ref_clock = ref_clock or ClockModel()
    pairs = match_by_key(ref_events, src_events)
    residuals = [src_clock.to_reference(ts) - ref_clock.to_reference(tr)
                 for ts, tr in pairs]
    return residual_stats(residuals)


# ---------------------------------------------------------------- 合并

def _nominal_interval(source):
    if source.nominal_interval:
        return source.nominal_interval
    times = sorted(e.t for e in source.events)
    if len(times) < 2:
        return None
    diffs = [b - a for a, b in zip(times, times[1:]) if b > a]
    return median(diffs) if diffs else None


def merge(sources, bucket_width, gap_factor=3.0, reference=None):
    """按对齐后的参考时间合并多个来源。

    - 分桶归属规则：bucket = floor(aligned_time / bucket_width)，仅标注归属，
      不重采样、不丢弃任何事件；
    - 连续采样来源的长间隔（> gap_factor * 标称间隔）记为 MissingSegment；
    - 若事件带 key 且指定 reference，则计算各来源相对参考的对齐残差。
    """
    records = []
    for src in sources:
        for e in src.events:
            aligned = src.clock.to_reference(e.t)
            records.append(MergedRecord(
                aligned_time=aligned,
                bucket=math.floor(aligned / bucket_width),
                source=src.name,
                event=e,
            ))
    records.sort(key=lambda r: (r.aligned_time, r.source))

    missing = []
    for src in sources:
        if not src.continuous:
            continue  # 事件驱动来源：无事件 ≠ 缺失
        interval = _nominal_interval(src)
        if not interval:
            continue
        aligned = sorted(src.clock.to_reference(e.t) for e in src.events)
        threshold = gap_factor * interval / (1.0 + src.clock.drift)
        for t0, t1 in zip(aligned, aligned[1:]):
            if t1 - t0 > threshold:
                missing.append(MissingSegment(
                    source=src.name,
                    start_ref=t0,
                    end_ref=t1,
                    expected_samples=max(0, int((t1 - t0) / interval) - 1),
                ))

    quality = {}
    if reference is not None:
        ref_src = next((s for s in sources if s.name == reference), None)
        if ref_src is not None:
            for src in sources:
                if src.name == reference:
                    continue
                stats = alignment_quality(ref_src.events, src.events,
                                          src.clock, ref_src.clock)
                if stats.get("n"):
                    quality[src.name] = stats

    return MergedTimeline(records=records, bucket_width=bucket_width,
                          missing_segments=missing, quality=quality)


# ---------------------------------------------------------------- 不变量

def check_invariants(merged, sources):
    """合并不变量：每个来源的事件在结果中出现次数 == 输入次数。"""
    counts = Counter(r.source for r in merged.records)
    for src in sources:
        expected = len(src.events)
        actual = counts.get(src.name, 0)
        assert actual == expected, (
            f"来源 {src.name}: 输入 {expected} 条, 结果 {actual} 条")
    assert len(merged.records) == sum(len(s.events) for s in sources), \
        "总记录数不等于各来源输入之和"
    return True
