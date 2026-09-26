"""内置离散事件模拟器: 用模拟数据校准解析模型。

实现方式: 服务者空闲时间最小堆 + 逐到达处理, 复杂度 O(n log c)。
队列长度通过对 (入队, 离队) 事件做扫描线积分得到时间平均值。

支持:
- 到达过程: poisson (泊松) / constant (均匀) / burst (周期性突发, 模拟流量尖峰)
- 服务时间: cv=1 指数, cv=0 定长, 其他 cv 为对数正态 (刻画长尾请求)
- 超时处理: abandon=True 时等待超时的请求离队 (Erlang A 式放弃, 贴近真实系统);
  abandon=False 时仍被服务, 仅统计"等待是否超过 timeout",
  用于与稳态解析模型 (无放弃) 做同口径校准。
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SimResult:
    n_arrivals: int = 0
    served: int = 0
    timeouts: int = 0
    timeout_prob: float = 0.0     # 超时概率 (含放弃请求)
    mean_wait: float = 0.0        # 已服务请求的平均排队等待
    p95_wait: float = 0.0
    p99_wait: float = 0.0
    mean_queue: float = 0.0       # 时间平均队列长度
    max_queue: int = 0
    utilization: float = 0.0
    throughput: float = 0.0       # 完成请求 / 秒
    window: float = 0.0           # 统计窗口 (秒)


def _percentile(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, math.ceil(q * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def simulate(lam: float, service_mean: float, c: int, cv: float = 1.0,
             timeout: Optional[float] = None, n_arrivals: int = 20000,
             arrival: str = "poisson", burst_size: int = 100,
             burst_interval: float = 1.0, abandon: bool = True, seed: int = 42,
             warmup_frac: float = 0.1) -> SimResult:
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
    if n_arrivals < 1:
        raise ValueError("n_arrivals 必须 >= 1")

    res = SimResult()
    if lam == 0.0:
        return res  # 零流量: 无事件

    rng = random.Random(seed)

    # --- 生成到达时刻 ---
    if arrival == "poisson":
        t = 0.0
        arrivals = []
        for _ in range(n_arrivals):
            t += rng.expovariate(lam)
            arrivals.append(t)
        eff_rate = lam
    elif arrival == "constant":
        arrivals = [(i + 1) / lam for i in range(n_arrivals)]
        eff_rate = lam
    elif arrival == "burst":
        if burst_size < 1 or burst_interval <= 0:
            raise ValueError("burst_size >= 1 且 burst_interval > 0")
        n_bursts = max(1, n_arrivals // burst_size)
        arrivals = []
        for k in range(n_bursts):
            base = (k + 1) * burst_interval
            arrivals.extend([base] * burst_size)
        n_arrivals = len(arrivals)
        eff_rate = burst_size / burst_interval
    else:
        raise ValueError(f"未知到达过程: {arrival}")

    # --- 服务时间生成器 ---
    if cv == 0.0:
        def gen_service() -> float:
            return service_mean
    elif cv == 1.0:
        mu = 1.0 / service_mean
        def gen_service() -> float:
            return rng.expovariate(mu)
    else:
        sigma2 = math.log(1.0 + cv * cv)
        mu_log = math.log(service_mean) - sigma2 / 2.0
        sigma = math.sqrt(sigma2)
        def gen_service() -> float:
            return rng.lognormvariate(mu_log, sigma)

    horizon_est = n_arrivals / eff_rate
    warmup = warmup_frac * horizon_est   # 丢弃预热期, 减少初始空系统偏差

    servers = [0.0] * c                  # 每个服务者的空闲时刻 (最小堆)
    waits: List[float] = []
    q_events: List = []                  # (时刻, +1/-1) 队列长度变化
    served = 0
    timeouts = 0
    service_sum = 0.0

    for t in arrivals:
        s = gen_service()
        free = heapq.heappop(servers)
        start = free if free > t else t
        wait = start - t
        if timeout is not None and abandon and wait > timeout:
            # 等待超时, 放弃: 服务者不被占用
            heapq.heappush(servers, free)
            if t >= warmup:
                timeouts += 1
                q_events.append((t, 1))
                q_events.append((t + timeout, -1))
            continue
        heapq.heappush(servers, start + s)
        if t >= warmup:
            served += 1
            if timeout is not None and wait > timeout:
                timeouts += 1   # 不放弃模式: 仍服务, 只记超时
            waits.append(wait)
            service_sum += s
            if wait > 0.0:
                q_events.append((t, 1))
                q_events.append((start, -1))

    end = max(max(servers), arrivals[-1])
    window = end - warmup

    # --- 扫描线求时间平均队列长度 ---
    q_events.sort()
    area = 0.0
    cur = 0
    last = warmup
    maxq = 0
    for tt, delta in q_events:
        area += cur * (tt - last)
        last = tt
        cur += delta
        if cur > maxq:
            maxq = cur
    area += cur * (end - last)

    waits.sort()
    # abandon=True: 超时请求未被服务, 总数 = served + timeouts
    # abandon=False: 超时请求仍被服务, 已计入 served
    total = served + timeouts if abandon else served
    res.n_arrivals = n_arrivals
    res.served = served
    res.timeouts = timeouts
    res.timeout_prob = timeouts / total if total else 0.0
    res.mean_wait = sum(waits) / len(waits) if waits else 0.0
    res.p95_wait = _percentile(waits, 0.95)
    res.p99_wait = _percentile(waits, 0.99)
    res.mean_queue = area / window if window > 0 else 0.0
    res.max_queue = maxq
    res.utilization = service_sum / (c * window) if window > 0 else 0.0
    res.throughput = served / window if window > 0 else 0.0
    res.window = window
    return res
