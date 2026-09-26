"""容量模型: M/M/c (Erlang C 精确解) 与 M/G/c (Allen-Cunneen 近似)。

模型与适用条件
--------------
- 到达过程: 泊松到达 (速率 lam, 请求/秒), 即到达间隔独立同分布且为指数分布。
- 服务时间: 均值 service_mean 秒 (mu = 1/service_mean), 变化系数 cv = 标准差/均值。
  cv = 1 时为指数服务, M/M/c 有精确解 (Erlang C);
  cv != 1 时用 Allen-Cunneen 近似 (M/G/c), cv <= 2 时误差通常 < 10%。
- 并发: c 个服务线程, 队列无限, FIFO。
- 稳定条件: rho = lam / (c*mu) < 1。rho >= 1 时队列发散,
  模型只给出流体近似 (fluid approximation) 并标记 stable=False。

突发流量: 以峰值到达率 (基础速率 x 峰值系数) 作为 lam 输入;
长尾请求: 用 cv > 1 刻画 (如对数正态服务时间), cv 越大解析近似越差,
应以模拟器 (simulator.py) 为准。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CapacityMetrics:
    lam: float
    service_mean: float
    c: int
    cv: float
    timeout: Optional[float]
    offered_load: float = 0.0      # a = lam/mu, 单位 Erlang
    utilization: float = 0.0       # rho = a/c
    stable: bool = True
    p_wait: float = 0.0            # 到达需要排队的概率 (Erlang C)
    mean_queue: float = 0.0        # 平均排队人数 Lq
    mean_wait: float = 0.0         # 平均排队等待 Wq (秒)
    mean_response: float = 0.0     # Wq + service_mean
    p_timeout: float = 0.0         # P(排队等待 > timeout)
    warnings: List[str] = field(default_factory=list)


def _validate(lam: float, service_mean: float, c: int, cv: float,
              timeout: Optional[float]) -> None:
    if lam < 0:
        raise ValueError("lam (到达率) 必须 >= 0")
    if service_mean <= 0:
        raise ValueError("service_mean (平均处理时长) 必须 > 0")
    if not isinstance(c, int) or isinstance(c, bool) or c < 1:
        raise ValueError("c (并发数) 必须为 >= 1 的整数")
    if cv < 0:
        raise ValueError("cv (服务时间变化系数) 必须 >= 0")
    if timeout is not None and timeout < 0:
        raise ValueError("timeout 必须 >= 0")


def erlang_c(a: float, c: int) -> float:
    """Erlang C 公式: M/M/c 中到达需要排队等待的概率。a = lam/mu (Erlang)。"""
    if a <= 0.0:
        return 0.0
    if a >= c:
        return 1.0
    # 用对数计算避免 a^c/c! 溢出
    log_terms = [k * math.log(a) - math.lgamma(k + 1) for k in range(c + 1)]
    m = max(log_terms)
    head = sum(math.exp(t - m) for t in log_terms[:-1])   # sum_{k=0}^{c-1} a^k/k!
    tail = math.exp(log_terms[c] - m) * c / (c - a)       # a^c/c! * c/(c-a)
    return tail / (head + tail)


def capacity_metrics(lam: float, service_mean: float, c: int, cv: float = 1.0,
                     timeout: Optional[float] = None) -> CapacityMetrics:
    """由到达率、处理时长与并发上限推导排队长度、等待时间与超时概率。"""
    _validate(lam, service_mean, c, cv, timeout)
    mu = 1.0 / service_mean
    res = CapacityMetrics(lam=lam, service_mean=service_mean, c=c, cv=cv,
                          timeout=timeout)
    if lam == 0.0:
        res.warnings.append("零流量: 所有排队指标为 0")
        return res

    a = lam / mu
    rho = a / c
    res.offered_load = a
    res.utilization = rho

    if rho >= 1.0:
        # 不稳定: 队列无限增长, 解析量无意义, 只给流体近似
        res.stable = False
        res.p_wait = 1.0
        res.mean_queue = math.inf
        res.mean_wait = math.inf
        res.mean_response = math.inf
        res.p_timeout = 1.0 - 1.0 / rho if timeout else 1.0
        res.warnings.append(
            f"系统不稳定: rho={rho:.2f} >= 1, 队列无限增长; "
            "p_timeout 为流体近似 (长期超时比例 = 1 - 1/rho)")
        return res

    pw = erlang_c(a, c)
    g = 0.5 * (1.0 + cv * cv)            # Allen-Cunneen 修正因子
    wq_mm = pw / (c * mu - lam)          # M/M/c 平均排队等待
    wq = wq_mm * g
    res.p_wait = min(1.0, pw * g)
    res.mean_wait = wq
    res.mean_queue = wq * lam            # Little 定律: Lq = lam * Wq
    res.mean_response = wq + service_mean
    if timeout is not None:
        # M/M/c 中 P(Wq > t) = Pw * exp(-(c*mu - lam) * t), 再乘修正因子近似
        res.p_timeout = min(1.0, pw * g * math.exp(-(c * mu - lam) * timeout))

    if rho > 0.9:
        res.warnings.append(
            f"利用率 rho={rho:.2f} > 0.9: 等待时间对参数误差极度敏感, 必须留余量")
    if cv > 2.0:
        res.warnings.append(
            f"cv={cv:.1f} > 2: Allen-Cunneen 近似误差大, 请以模拟器结果为准")
    return res
