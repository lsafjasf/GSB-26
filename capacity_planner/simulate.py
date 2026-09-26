"""内置离散事件模拟器（仅标准库），用于校准解析模型。

- 到达过程：泊松（指数间隔），可选一段突发窗口（窗口内速率提升）。
- 服务时间分布：exponential / lognormal（长尾）/ constant。
- 系统：c 个服务台（线程），可选有限队列（溢出即丢弃），可选等待超时。
- 输出：平均/分位等待、时间平均队列长、超时率、丢弃率、吞吐。
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass, field

from .models import ModelError


@dataclass
class SimConfig:
    lam: float                      # 基线到达率 (1/s)
    c: int                          # 服务台/线程数
    mean_service: float             # 平均处理时长 (s)
    dist: str = "exponential"       # exponential | lognormal | constant
    scv_service: float = 1.0        # lognormal 的 SCV（长尾时 >1）
    queue_cap: int | None = None    # 排队位置上限（None=无限），溢出丢弃
    timeout: float | None = None    # 排队等待超时（秒），超时计入 timed_out
    sim_time: float = 2000.0        # 模拟时长（秒）
    warmup: float = 0.1             # 预热比例，丢弃前段统计
    seed: int = 42
    burst: tuple | None = None      # (start, duration, lam_burst)


@dataclass
class SimResult:
    arrived: int = 0
    served: int = 0
    dropped: int = 0                # 队列满被拒绝
    timed_out: int = 0              # 排队等待超过 timeout
    mean_wait: float = 0.0          # 平均排队等待（被服务者）
    p95_wait: float = 0.0
    max_wait: float = 0.0
    mean_queue: float = 0.0         # 时间平均排队长度
    max_queue: int = 0
    utilization: float = 0.0
    throughput: float = 0.0
    p_timeout: float = 0.0          # timed_out / arrived
    p_drop: float = 0.0
    sim_time: float = 0.0
    notes: list = field(default_factory=list)


def _validate(cfg: SimConfig) -> None:
    if cfg.lam < 0 or not math.isfinite(cfg.lam):
        raise ModelError(f"lam 必须为非负有限数，得到 {cfg.lam!r}")
    if not isinstance(cfg.c, int) or cfg.c < 1:
        raise ModelError(f"c 必须为 >=1 的整数，得到 {cfg.c!r}")
    if cfg.mean_service <= 0 or not math.isfinite(cfg.mean_service):
        raise ModelError(f"mean_service 必须为正，得到 {cfg.mean_service!r}")
    if cfg.dist not in ("exponential", "lognormal", "constant"):
        raise ModelError(f"未知分布 {cfg.dist!r}")
    if cfg.scv_service < 0:
        raise ModelError("scv_service 必须 >= 0")
    if cfg.queue_cap is not None and cfg.queue_cap < 0:
        raise ModelError("queue_cap 必须 >= 0 或 None")
    if cfg.timeout is not None and cfg.timeout < 0:
        raise ModelError("timeout 必须 >= 0 或 None")
    if cfg.sim_time <= 0:
        raise ModelError("sim_time 必须为正")
    if not 0.0 <= cfg.warmup < 0.9:
        raise ModelError("warmup 必须在 [0, 0.9)")
    if cfg.burst is not None:
        start, dur, lam_b = cfg.burst
        if start < 0 or dur <= 0 or lam_b < 0:
            raise ModelError("burst=(start, duration, lam_burst) 参数非法")


def _service_sampler(cfg: SimConfig, rng: random.Random):
    if cfg.dist == "exponential":
        return lambda: rng.expovariate(1.0 / cfg.mean_service)
    if cfg.dist == "constant":
        return lambda: cfg.mean_service
    # lognormal：由均值与 SCV 反解参数
    sigma2 = math.log(1.0 + cfg.scv_service)
    mu_ln = math.log(cfg.mean_service) - sigma2 / 2.0
    sigma = math.sqrt(sigma2)
    return lambda: rng.lognormvariate(mu_ln, sigma)


def _arrival_times(cfg: SimConfig, rng: random.Random):
    """生成 [0, sim_time] 内的到达时刻（分段泊松）。"""
    times = []
    segments = [(0.0, cfg.sim_time, cfg.lam)]
    if cfg.burst is not None:
        start, dur, lam_b = cfg.burst
        end = min(start + dur, cfg.sim_time)
        segments = []
        if start > 0:
            segments.append((0.0, start, cfg.lam))
        if end > start:
            segments.append((start, end, lam_b))
        if end < cfg.sim_time:
            segments.append((end, cfg.sim_time, cfg.lam))
    for seg_start, seg_end, rate in segments:
        if rate <= 0:
            continue
        t = seg_start + rng.expovariate(rate)
        while t < seg_end:
            times.append(t)
            t += rng.expovariate(rate)
    times.sort()
    return times


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def run_simulation(cfg: SimConfig) -> SimResult:
    _validate(cfg)
    rng = random.Random(cfg.seed)
    sample_service = _service_sampler(cfg, rng)
    arrivals = _arrival_times(cfg, rng)

    warmup_end = cfg.sim_time * cfg.warmup
    busy = 0
    queue = deque()           # 元素为到达时刻
    departures = []           # 完成时刻堆
    waits = []
    area_queue = 0.0          # 队列长度对时间积分
    area_busy = 0.0
    last_t = 0.0
    res = SimResult(sim_time=cfg.sim_time)

    def advance_clock(now):
        nonlocal last_t, area_queue, area_busy
        t_now = min(now, cfg.sim_time)
        t_from = max(last_t, warmup_end)
        dt = t_now - t_from
        if dt > 0:
            area_queue += len(queue) * dt
            area_busy += busy * dt
        last_t = now

    arr_idx = 0
    n_arr = len(arrivals)
    now = 0.0
    while arr_idx < n_arr or departures:
        next_arr = arrivals[arr_idx] if arr_idx < n_arr else math.inf
        next_dep = departures[0] if departures else math.inf
        if next_arr <= next_dep:
            now = next_arr
            arr_idx += 1
            advance_clock(now)
            if now > cfg.sim_time:
                break
            # 到达事件
            if now >= warmup_end:
                res.arrived += 1
            if busy < cfg.c:
                busy += 1
                if now >= warmup_end:
                    waits.append(0.0)
                    res.served += 1
                heapq.heappush(departures, now + sample_service())
            elif cfg.queue_cap is None or len(queue) < cfg.queue_cap:
                queue.append(now)
                if len(queue) > res.max_queue:
                    res.max_queue = len(queue)
            else:
                if now >= warmup_end:
                    res.dropped += 1
        else:
            now = heapq.heappop(departures)
            advance_clock(now)
            if now > cfg.sim_time:
                break
            # 完成事件：释放服务台，若有人在排队则立即顶替
            busy -= 1
            if queue:
                t0 = queue.popleft()
                wait = now - t0
                if t0 >= warmup_end:
                    waits.append(wait)
                    res.served += 1
                    if cfg.timeout is not None and wait > cfg.timeout:
                        res.timed_out += 1
                busy += 1
                heapq.heappush(departures, now + sample_service())

    horizon = max(cfg.sim_time - warmup_end, 1e-12)
    waits.sort()
    res.mean_wait = sum(waits) / len(waits) if waits else 0.0
    res.p95_wait = _percentile(waits, 0.95)
    res.max_wait = waits[-1] if waits else 0.0
    res.mean_queue = area_queue / horizon
    res.utilization = min(1.0, area_busy / (horizon * cfg.c))
    res.throughput = res.served / horizon
    if res.arrived > 0:
        res.p_timeout = res.timed_out / res.arrived
        res.p_drop = res.dropped / res.arrived
    if cfg.lam > 0 and cfg.lam / (cfg.c / cfg.mean_service) > 0.9:
        res.notes.append("rho>0.9：模拟结果对随机种子敏感，需更长 sim_time")
    return res
