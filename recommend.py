"""资源配额建议: 线程数 / 队列长度 / 内存 / 连接数, 每项附不确定度与依据。

方法:
- 线程数: 在峰值到达率下, 满足 rho <= max_util 且 P(等待 > timeout) <= 目标
  的最小 c (用 capacity_model 的 M/G/c 近似逐个搜索)。
- 队列长度: max(超时时间内净排空位数, 突发大小)。
  净排空速率 = c*mu - lam_peak, 排在第 k 位的等待约 k / (c*mu - lam_peak)。
- 内存: 线程栈 + 排队请求 + 连接 (在途 + 排队) 三部分线性相加。
- 不确定度: 到达率 / 处理时长各 +/-20% 重新求解, 给出敏感区间;
  并可传入模拟校准偏差 (calib_dev) 作为模型误差的量化依据。
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from capacity_model import CapacityMetrics, capacity_metrics


@dataclass
class Quota:
    threads: int
    queue: int
    memory_mb: float
    connections: int
    threads_range: Tuple[Optional[int], Optional[int]]
    predicted: CapacityMetrics
    rationale: List[str] = field(default_factory=list)
    uncertainty: List[str] = field(default_factory=list)


def _min_threads(lam: float, service_mean: float, cv: float, timeout: float,
                 target_p: float, max_util: float) -> Optional[int]:
    c_min = max(1, math.ceil(lam * service_mean / max_util))
    limit = max(1000, 20 * c_min)
    for cand in range(c_min, limit + 1):
        m = capacity_metrics(lam, service_mean, cand, cv=cv, timeout=timeout)
        if m.utilization <= max_util and m.p_timeout <= target_p:
            return cand
    return None


def recommend(lam: float, service_mean: float, cv: float = 1.0,
              timeout: float = 1.0, target_p_timeout: float = 1e-3,
              max_util: float = 0.8, peak_factor: float = 1.5,
              burst_size: int = 0, mem_per_thread_mb: float = 1.0,
              mem_per_request_mb: float = 0.05, mem_per_conn_mb: float = 0.02,
              calib_dev: float = 0.0) -> Quota:
    if lam <= 0:
        raise ValueError("lam 必须 > 0 才能给出配额建议 (零流量无需容量规划)")
    if service_mean <= 0:
        raise ValueError("service_mean 必须 > 0")
    if timeout <= 0:
        raise ValueError("timeout 必须 > 0")
    if not 0 < target_p_timeout < 1:
        raise ValueError("target_p_timeout 必须在 (0, 1) 内")
    if not 0 < max_util < 1:
        raise ValueError("max_util 必须在 (0, 1) 内")
    if peak_factor < 1:
        raise ValueError("peak_factor 必须 >= 1")
    if burst_size < 0:
        raise ValueError("burst_size 必须 >= 0")

    lam_peak = lam * peak_factor
    mu = 1.0 / service_mean
    rationale: List[str] = []
    uncertainty: List[str] = []

    # 1) 线程数
    c = _min_threads(lam_peak, service_mean, cv, timeout, target_p_timeout, max_util)
    if c is None:
        c = max(1000, math.ceil(20 * lam_peak * service_mean / max_util))
        rationale.append(
            f"线程数={c}: 已达搜索上限仍无法满足 P(等待>{timeout}s)<={target_p_timeout}, "
            "需放宽超时目标或降低处理时长")
    else:
        rationale.append(
            f"线程数={c}: 峰值 {lam_peak:.1f} req/s 下满足 rho<={max_util} 且 "
            f"P(等待>{timeout}s)<={target_p_timeout} (M/G/c 近似, cv={cv})")

    # 2) 队列长度
    predicted = capacity_metrics(lam_peak, service_mean, c, cv=cv, timeout=timeout)
    drain = max(c * mu - lam_peak, 1e-9)
    q_timeout = math.ceil(timeout * drain)
    queue = max(q_timeout, burst_size)
    rationale.append(
        f"队列={queue}: 超时 {timeout}s 内净排空 {q_timeout} 位 "
        f"(净排空速率 {drain:.1f}/s), 并吸收突发 {burst_size} 个请求")

    # 3) 连接数与内存
    conns = c + queue
    mem = (c * mem_per_thread_mb + queue * mem_per_request_mb
           + conns * mem_per_conn_mb)
    rationale.append(
        f"连接数={conns} (在途 {c} + 排队 {queue}); "
        f"内存={mem:.1f}MB = 线程 {c}x{mem_per_thread_mb} + "
        f"队列 {queue}x{mem_per_request_mb} + 连接 {conns}x{mem_per_conn_mb}")

    # 4) 不确定度: 参数敏感性
    hi = _min_threads(lam_peak * 1.2, service_mean * 1.2, cv, timeout,
                      target_p_timeout, max_util)
    lo = _min_threads(lam_peak * 0.8, service_mean, cv, timeout,
                      target_p_timeout, max_util)
    uncertainty.append(
        f"线程敏感区间 [{lo}, {hi}]: 到达率 -20% / 到达率与处理时长各 +20% 的重估")
    if calib_dev > 0:
        uncertainty.append(
            f"模拟校准偏差 {calib_dev:.0%}: 解析模型与模拟器的相对误差, "
            "建议按此比例放大配额")
    uncertainty.append(
        f"cv={cv}: " + ("Allen-Cunneen 近似可信 (cv<=2)" if cv <= 2
                        else "长尾显著, 解析近似误差大, 以模拟器为准"))

    return Quota(threads=c, queue=queue, memory_mb=mem, connections=conns,
                 threads_range=(lo, hi), predicted=predicted,
                 rationale=rationale, uncertainty=uncertainty)


def main() -> None:
    ap = argparse.ArgumentParser(description="资源容量配额建议")
    ap.add_argument("--rate", type=float, required=True, help="平均到达率 req/s")
    ap.add_argument("--service-ms", type=float, required=True, help="平均处理时长 ms")
    ap.add_argument("--cv", type=float, default=1.0, help="处理时长变化系数")
    ap.add_argument("--timeout", type=float, default=1.0, help="排队超时 s")
    ap.add_argument("--target", type=float, default=1e-3, help="目标超时概率")
    ap.add_argument("--max-util", type=float, default=0.8, help="目标利用率上限")
    ap.add_argument("--peak-factor", type=float, default=1.5, help="峰值系数")
    ap.add_argument("--burst", type=int, default=0, help="突发请求数")
    ap.add_argument("--calib-dev", type=float, default=0.0, help="模拟校准偏差")
    args = ap.parse_args()

    q = recommend(args.rate, args.service_ms / 1000.0, cv=args.cv,
                  timeout=args.timeout, target_p_timeout=args.target,
                  max_util=args.max_util, peak_factor=args.peak_factor,
                  burst_size=args.burst, calib_dev=args.calib_dev)
    print("=== 资源配额建议 ===")
    print(f"线程数:   {q.threads}  (敏感区间 {q.threads_range[0]} ~ {q.threads_range[1]})")
    print(f"队列长度: {q.queue}")
    print(f"连接数:   {q.connections}")
    print(f"内存:     {q.memory_mb:.1f} MB")
    print(f"预测: rho={q.predicted.utilization:.2f} "
          f"Wq={q.predicted.mean_wait * 1000:.1f}ms "
          f"P(超时)={q.predicted.p_timeout:.2e}")
    print("--- 依据 ---")
    for r in q.rationale:
        print(f"  * {r}")
    print("--- 不确定度 ---")
    for u in q.uncertainty:
        print(f"  * {u}")


if __name__ == "__main__":
    main()
