"""排队论容量模型（仅标准库）。

模型与适用条件
--------------
1. M/M/c (Erlang C)：泊松到达 + 指数服务 + c 个并行服务台 + 无限队列。
   闭式解，要求 rho = lam/(c*mu) < 1。服务时间近似指数、到达近似独立时准确。
2. M/G/c (Allen-Cunneen 近似)：任意服务时间分布（给定均值与平方变异系数
   SCV = Var/Mean^2），把 M/M/c 的平均排队等待乘以 (1+SCV)/2。
   适用于 0 <= SCV <= ~4 且 rho 不贴近 1；长尾(lognormal 等)高 SCV 时偏乐观。
3. M/M/c/K：有限队列，生灭链精确平稳分布，用于估算拒绝/溢出概率与队列配额。
4. 突发流体模型 (fluid overload)：突发期 lam_b > c*mu 时积压确定性增长，
   用于估算突发下的最大积压、最大等待与超时比例。忽略随机波动，是下界估计。

模型失效边界
------------
- rho -> 1 时平均等待 ~ 1/(c*mu - lam) 发散，对 lam/mu 的测量误差极度敏感。
- rho >= 1 时平稳分布不存在，模型输出无意义（本模块会显式报告 unstable）。
- 到达过程强相关（如重试风暴）或服务时间重尾且 SCV 很大时，近似误差可超 50%。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


class ModelError(ValueError):
    """非法参数或模型不适用。"""


def _check_positive(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ModelError(f"{name} 必须是数值，得到 {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise ModelError(f"{name} 必须为正有限数，得到 {value!r}")


def _check_non_negative(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ModelError(f"{name} 必须是数值，得到 {value!r}")
    if not math.isfinite(value) or value < 0:
        raise ModelError(f"{name} 必须为非负有限数，得到 {value!r}")


def _check_capacity(c: int) -> None:
    if not isinstance(c, int) or isinstance(c, bool) or c < 1:
        raise ModelError(f"并发上限 c 必须为 >=1 的整数，得到 {c!r}")


@dataclass
class QueueMetrics:
    """稳态排队指标。unstable=True 时其余字段无意义。"""
    rho: float                # 利用率 lam/(c*mu)
    p_wait: float             # 到达需要排队的概率 (Erlang C)
    lq: float                 # 平均排队人数
    wq: float                 # 平均排队等待（秒）
    w: float                  # 平均逗留时间（秒）
    unstable: bool = False
    notes: list = field(default_factory=list)


def erlang_b(c: int, a: float) -> float:
    """Erlang B 阻塞概率（递归计算，数值稳定）。a 为爱尔兰负载。"""
    _check_capacity(c)
    _check_non_negative("offered load a", a)
    b = 1.0
    for n in range(1, c + 1):
        b = (a * b) / (n + a * b)
    return b


def erlang_c(c: int, a: float) -> float:
    """Erlang C：到达需等待的概率。要求 a < c。"""
    _check_capacity(c)
    _check_non_negative("offered load a", a)
    if a >= c:
        return 1.0
    b = erlang_b(c, a)
    rho = a / c
    denom = 1.0 - rho * (1.0 - b)
    return b / denom if denom > 0 else 1.0


def mmc_metrics(lam: float, mu: float, c: int) -> QueueMetrics:
    """M/M/c 稳态指标。lam: 到达率(1/s)，mu: 单服务台服务率(1/s)。"""
    _check_non_negative("arrival rate lam", lam)
    _check_positive("service rate mu", mu)
    _check_capacity(c)
    if lam == 0.0:
        return QueueMetrics(rho=0.0, p_wait=0.0, lq=0.0, wq=0.0,
                            w=1.0 / mu, notes=["零流量：无排队"])
    a = lam / mu
    rho = a / c
    if rho >= 1.0:
        return QueueMetrics(rho=rho, p_wait=1.0, lq=math.inf, wq=math.inf,
                            w=math.inf, unstable=True,
                            notes=["rho>=1：系统不稳定，队列无界增长"])
    pw = erlang_c(c, a)
    wq = pw / (c * mu - lam)
    lq = lam * wq
    return QueueMetrics(rho=rho, p_wait=pw, lq=lq, wq=wq, w=wq + 1.0 / mu)


def mgc_metrics(lam: float, mean_service: float, scv_service: float,
                c: int) -> QueueMetrics:
    """M/G/c 的 Allen-Cunneen 近似。

    lam: 到达率(1/s)；mean_service: 平均处理时长(秒)；
    scv_service: 服务时间平方变异系数（指数分布=1，定长=0，长尾>1）。
    """
    _check_non_negative("arrival rate lam", lam)
    _check_positive("mean service time", mean_service)
    _check_non_negative("service SCV", scv_service)
    _check_capacity(c)
    if scv_service > 4.0:
        note = "SCV>4：重尾服务，Allen-Cunneen 近似可能显著偏乐观"
    else:
        note = None
    mu = 1.0 / mean_service
    base = mmc_metrics(lam, mu, c)
    if base.unstable or lam == 0.0:
        if note:
            base.notes.append(note)
        return base
    factor = (1.0 + scv_service) / 2.0
    wq = base.wq * factor
    lq = lam * wq
    notes = list(base.notes)
    if note:
        notes.append(note)
    return QueueMetrics(rho=base.rho, p_wait=base.p_wait, lq=lq, wq=wq,
                        w=wq + mean_service, notes=notes)


def p_wait_exceeds(m: QueueMetrics, c: int, mu: float, t: float) -> float:
    """P(排队等待 > t) 的近似：P_wait * exp(-(c*mu - lam)*t)（M/M/c 精确，
    M/G/c 下沿用同一指数衰减作近似）。unstable 时返回 1。"""
    _check_non_negative("threshold t", t)
    if m.unstable:
        return 1.0
    if m.p_wait == 0.0:
        return 0.0
    # 由 wq = p_wait/(c*mu - lam) 反推衰减率，兼容 M/G/c 调整后的 wq
    if m.wq <= 0.0:
        return 0.0
    decay = m.p_wait / m.wq
    return m.p_wait * math.exp(-decay * t)


@dataclass
class FiniteQueueMetrics:
    """M/M/c/K 指标（K 为系统总容量 = c 服务 + 队列）。"""
    p_block: float      # 到达被拒绝概率
    throughput: float   # 有效吞吐 (1/s)
    lq: float
    wq: float


def mmck_metrics(lam: float, mu: float, c: int, k: int) -> FiniteQueueMetrics:
    """M/M/c/K 生灭链精确解。k >= c 为系统可容纳的最大请求数。"""
    _check_non_negative("arrival rate lam", lam)
    _check_positive("service rate mu", mu)
    _check_capacity(c)
    if not isinstance(k, int) or k < c:
        raise ModelError(f"K 必须为 >= c 的整数，得到 {k!r}")
    if lam == 0.0:
        return FiniteQueueMetrics(p_block=0.0, throughput=0.0, lq=0.0, wq=0.0)
    a = lam / mu
    rho = a / c
    # 用对数尺度连乘避免大 K 下溢出
    log_p = [0.0]  # log(p_n / p_0)
    for n in range(1, k + 1):
        if n <= c:
            log_p.append(log_p[-1] + math.log(a) - math.log(n))
        else:
            log_p.append(log_p[-1] + math.log(rho))
    mx = max(log_p)
    weights = [math.exp(x - mx) for x in log_p]
    total = sum(weights)
    p = [w / total for w in weights]
    p_block = p[k]
    throughput = lam * (1.0 - p_block)
    lq = sum((n - c) * p[n] for n in range(c + 1, k + 1))
    wq = lq / throughput if throughput > 0 else 0.0
    return FiniteQueueMetrics(p_block=p_block, throughput=throughput,
                              lq=lq, wq=wq)


@dataclass
class BurstMetrics:
    """突发流体模型结果。"""
    overload: bool          # 突发期是否超载
    peak_backlog: float     # 峰值积压请求数
    max_wait: float         # 突发期内到达者的最大排队等待（秒）
    timeout_fraction: float # 突发期到达者中等待超过 timeout 的比例
    drain_time: float       # 突发结束后排空积压所需时间（秒）
    notes: list = field(default_factory=list)


def burst_overload(lam_base: float, lam_burst: float, mu: float, c: int,
                   duration: float, timeout: float) -> BurstMetrics:
    """突发流体模型：突发期 duration 秒、到达率 lam_burst，其余时间 lam_base。

    假设服务台满负荷恒速排水（确定性流体近似），忽略随机波动，
    因此给出的是积压/等待的乐观下界。
    """
    _check_non_negative("base rate", lam_base)
    _check_non_negative("burst rate", lam_burst)
    _check_positive("service rate mu", mu)
    _check_capacity(c)
    _check_positive("burst duration", duration)
    _check_non_negative("timeout", timeout)
    capacity = c * mu
    notes = ["流体近似：忽略随机波动，实际峰值更高"]
    if lam_burst <= capacity:
        return BurstMetrics(overload=False, peak_backlog=0.0, max_wait=0.0,
                            timeout_fraction=0.0, drain_time=0.0,
                            notes=["突发速率未超服务容量，无持续积压"] + notes)
    growth = lam_burst - capacity
    peak = growth * duration
    max_wait = peak / capacity
    drain = peak / (capacity - lam_base) if lam_base < capacity else math.inf
    if lam_base >= capacity:
        notes.append("基线流量已超容量：积压无法排空")
    # 到达时刻 t 的等待 = growth*t/capacity，超过 timeout 的到达比例
    if timeout <= 0.0:
        frac = 1.0
    else:
        t_star = timeout * capacity / growth
        frac = max(0.0, (duration - t_star) / duration)
    return BurstMetrics(overload=True, peak_backlog=peak, max_wait=max_wait,
                        timeout_fraction=frac, drain_time=drain, notes=notes)
