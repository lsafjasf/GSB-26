"""资源配额建议：线程、队列、内存、连接数，每项标注不确定度与依据。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import (ModelError, mgc_metrics, mmck_metrics, p_wait_exceeds,
                     burst_overload)

# 不确定度来源说明（写入报告，便于审计）
UNCERTAINTY_BASIS = {
    "threads": "到达率/处理时长测量误差 + rho 接近 1 时等待时间发散的敏感性",
    "queue": "M/M/c/K 精确解对 lam 的敏感性；突发场景用流体模型下界修正",
    "memory": "由线程/队列配额线性推导，不确定度继承自配额与单请求内存误差",
    "connections": "按峰值并发连接 = 服务中 + 排队 + 余量推导",
}


@dataclass
class QuotaItem:
    name: str
    value: float
    unit: str
    uncertainty: str      # 例如 "±30%"
    rationale: str


@dataclass
class Recommendation:
    items: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    feasible: bool = True

    def format(self) -> str:
        lines = []
        for it in self.items:
            lines.append(f"{it.name:<14}{it.value:>10.4g} {it.unit:<6}"
                         f"不确定度 {it.uncertainty:<22}依据: {it.rationale}")
        for w in self.warnings:
            lines.append(f"[警告] {w}")
        if not self.feasible:
            lines.append("[结论] 当前参数下不存在可行配额：需先降载或水平扩容")
        return "\n".join(lines)


def recommend(lam: float, mean_service: float, scv_service: float = 1.0,
              timeout: float = 1.0, target_p_timeout: float = 0.01,
              target_rho: float = 0.7, max_threads: int = 4096,
              mem_per_active_mb: float = 1.0, mem_per_queued_mb: float = 0.05,
              headroom: float = 1.3,
              burst: tuple | None = None) -> Recommendation:
    """生成配额建议。

    lam: 到达率(1/s)；mean_service: 平均处理时长(s)；scv_service: 服务 SCV；
    timeout: 排队超时(s)；target_p_timeout: 可接受超时概率；
    target_rho: 目标利用率上限；burst: (duration, lam_burst) 可选突发。
    """
    if lam < 0 or not math.isfinite(lam):
        raise ModelError(f"lam 非法: {lam!r}")
    if mean_service <= 0 or not math.isfinite(mean_service):
        raise ModelError(f"mean_service 非法: {mean_service!r}")
    if scv_service < 0:
        raise ModelError(f"scv_service 非法: {scv_service!r}")
    if timeout <= 0:
        raise ModelError(f"timeout 非法: {timeout!r}")
    if not 0 < target_p_timeout < 1 or not 0 < target_rho < 1:
        raise ModelError("target_p_timeout / target_rho 必须在 (0,1)")
    if mem_per_active_mb <= 0 or mem_per_queued_mb < 0:
        raise ModelError("单请求内存参数非法")

    rec = Recommendation()
    mu = 1.0 / mean_service

    # ---- 零流量 / 单请求 ----
    if lam == 0.0:
        rec.items.append(QuotaItem("threads", 1, "个", "n/a",
                                   "零流量：保留 1 个线程应对冷启动请求"))
        rec.items.append(QuotaItem("queue", 1, "槽位", "n/a",
                                   "零流量：最小队列"))
        rec.items.append(QuotaItem("memory_mb",
                                   mem_per_active_mb + mem_per_queued_mb,
                                   "MB", "n/a", "1 活跃 + 1 排队"))
        rec.warnings.append("零流量输入：配额为占位最小值，不代表真实需求")
        return rec

    # ---- 线程数：满足 rho 上限与超时概率的最小 c ----
    c_min_load = math.ceil(lam * mean_service)  # 稳定性下界
    if c_min_load >= max_threads:
        rec.feasible = False
        rec.warnings.append(
            f"稳定性所需线程 {c_min_load} 超上限 {max_threads}：单实例不可行")
    c = max(1, c_min_load)
    chosen = None
    while c <= max_threads:
        m = mgc_metrics(lam, mean_service, scv_service, c)
        if m.unstable:
            c += 1
            continue
        p_to = p_wait_exceeds(m, c, mu, timeout)
        if m.rho <= target_rho and p_to <= target_p_timeout:
            chosen = (c, m, p_to)
            break
        c += 1
    if chosen is None:
        rec.feasible = False
        rec.warnings.append(
            f"在 c<={max_threads} 内无法满足 rho<={target_rho} 且 "
            f"P(超时)<={target_p_timeout}；建议水平扩容或限流")
        c = min(max_threads, max(c_min_load, 1))
        m = mgc_metrics(lam, mean_service, scv_service, c)
        p_to = 1.0 if m.unstable else p_wait_exceeds(m, c, mu, timeout)
        chosen = (c, m, p_to)
    c_star, m_star, p_to_star = chosen

    # 不确定度：流量上浮 20% 时需要追加的线程数（高 rho 下会非线性放大）
    c_up = c_star
    while c_up <= max_threads:
        m_up = mgc_metrics(lam * 1.2, mean_service, scv_service, c_up)
        if not m_up.unstable and m_up.rho <= target_rho:
            break
        c_up += 1
    if c_up > max_threads:
        unc = "流量+20%即超上限"
    else:
        unc = f"+{c_up - c_star}线程(流量+20%)"
    rec.items.append(QuotaItem(
        "threads", c_star, "个", unc,
        f"满足 rho={m_star.rho:.2f}<={target_rho} 且 P(超时)={p_to_star:.2%}"
        f"<={target_p_timeout:.2%} 的最小线程数 (M/G/c 近似, SCV={scv_service})"))

    # ---- 队列长度：M/M/c/K 使阻塞率 <= target 的最小 K-c ----
    k = c_star
    queue_slots = 0
    while k - c_star < 100000:
        fq = mmck_metrics(lam, mu, c_star, k)
        if fq.p_block <= target_p_timeout:
            queue_slots = k - c_star
            break
        k += max(1, (k - c_star) // 2 + 1)
    burst_note = ""
    if burst is not None:
        dur, lam_b = burst
        bm = burst_overload(lam, lam_b, mu, c_star, dur, timeout)
        if bm.overload:
            burst_slots = math.ceil(bm.peak_backlog * headroom)
            if burst_slots > queue_slots:
                queue_slots = burst_slots
                burst_note = (f"；突发({lam_b}/s x {dur}s)峰值积压 "
                              f"{bm.peak_backlog:.0f} 主导队列配额")
            rec.warnings.append(
                f"突发超载：峰值积压 {bm.peak_backlog:.0f}，最大等待 "
                f"{bm.max_wait:.2f}s，突发期超时比例约 {bm.timeout_fraction:.1%}")
    rec.items.append(QuotaItem(
        "queue", queue_slots, "槽位", "±30%",
        f"M/M/c/K 阻塞率<={target_p_timeout:.2%} 的最小队列{burst_note}"))

    # ---- 内存：活跃请求 + 排队请求，加 headroom ----
    mem = (c_star * mem_per_active_mb + queue_slots * mem_per_queued_mb) * headroom
    rec.items.append(QuotaItem(
        "memory_mb", mem, "MB", "±40%",
        f"({c_star}活跃x{mem_per_active_mb}MB + {queue_slots}排队x"
        f"{mem_per_queued_mb}MB) x {headroom} 余量"))

    # ---- 连接数：峰值并发连接 ----
    conns = math.ceil((c_star + queue_slots) * headroom)
    rec.items.append(QuotaItem(
        "connections", conns, "个", "±30%",
        "峰值并发 = (线程 + 队列) x 余量；面向连接池/FD 上限规划"))

    if m_star.rho > 0.85:
        rec.warnings.append(
            f"rho={m_star.rho:.2f} 偏高：等待时间对流量误差极敏感，建议降低 "
            f"target_rho 或增加冗余")
    if scv_service > 2.0:
        rec.warnings.append(
            f"服务时间长尾(SCV={scv_service})：解析模型误差大，"
            f"上线前应以模拟/实测校准")
    return rec
