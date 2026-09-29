"""模拟校准: 同一参数下对比解析模型与模拟器, 偏差超阈值则告警。

对比指标: 平均排队等待 Wq, 时间平均队列长度 Lq, 超时概率 P(timeout)。
相对偏差 = |model - sim| / sim (sim 为 0 时退化为绝对差)。
默认阈值 15%: M/M/c 精确解应远小于此; 长尾 / 突发场景预期会触发告警,
这正是"解析模型失效边界"的量化体现。

注意: 模拟以 abandon=False (超时仍服务) 运行, 与稳态解析模型同口径;
否则放弃行为会改变队列动态 (Erlang A), 与 M/M/c 稳态解不可比。
"""

from __future__ import annotations

import argparse
import math
from typing import Dict, List, Tuple

from capacity_model import capacity_metrics
from simulator import simulate

THRESHOLD = 0.15


def required_arrivals(p: float, rel_tol: float = 0.15, z: float = 1.96) -> int:
    """二项估计 p_hat = k/n 达到给定相对误差所需的最小样本量 (正态近似)。

    由 Var(p_hat) = p(1-p)/n 解出 n >= z^2 (1-p) / (rel_tol^2 * p)。
    事件越稀有 (p 越小), 所需样本量按 1/p 增长:
    p=0.0075, rel_tol=15% 时需约 2.3 万; p=1e-4 时需约 384 万。

    注意: 排队系统中超时事件相互关联 (同一拥堵期内的请求成批超时),
    有效样本量小于到达数, 实测标准误为二项 SE 的 4~9 倍,
    实际需求应在此基础上再放大, 或用多次独立重复直接估计 SEM。
    """
    if p <= 0.0:
        return 0
    return math.ceil(z * z * (1.0 - p) / (rel_tol * rel_tol * p))


SCENARIOS = [
    dict(name="M/M/c 中等负载 rho=0.8", lam=8.0, service_mean=1.0, c=10,
         cv=1.0, timeout=2.0, arrival="poisson"),
    dict(name="M/M/c 高负载 rho=0.95", lam=9.5, service_mean=1.0, c=10,
         cv=1.0, timeout=2.0, arrival="poisson"),
    dict(name="M/G/c 长尾 cv=2", lam=8.0, service_mean=1.0, c=10,
         cv=2.0, timeout=2.0, arrival="poisson"),
    dict(name="突发流量 (每10s突发80个)", lam=8.0, service_mean=1.0, c=10,
         cv=1.0, timeout=2.0, arrival="burst", burst_size=80, burst_interval=10.0),
]


def _rel_dev(model_val: float, sim_val: float) -> float:
    if sim_val == 0.0:
        return 0.0 if model_val == 0.0 else math.inf
    return abs(model_val - sim_val) / abs(sim_val)


def calibrate(n_arrivals: int = 50000, threshold: float = THRESHOLD,
              seed: int = 42) -> Tuple[List[Dict], List[str]]:
    rows: List[Dict] = []
    alerts: List[str] = []
    for sc in SCENARIOS:
        model = capacity_metrics(sc["lam"], sc["service_mean"], sc["c"],
                                 cv=sc["cv"], timeout=sc["timeout"])
        sim = simulate(sc["lam"], sc["service_mean"], sc["c"], cv=sc["cv"],
                       timeout=sc["timeout"], n_arrivals=n_arrivals,
                       arrival=sc.get("arrival", "poisson"),
                       burst_size=sc.get("burst_size", 100),
                       burst_interval=sc.get("burst_interval", 1.0),
                       abandon=False, seed=seed)
        devs = {
            "mean_wait": _rel_dev(model.mean_wait, sim.mean_wait),
            "mean_queue": _rel_dev(model.mean_queue, sim.mean_queue),
            "p_timeout": _rel_dev(model.p_timeout, sim.timeout_prob),
        }
        worst = max(devs.values())
        row = dict(name=sc["name"], model=model, sim=sim, devs=devs, worst=worst)
        rows.append(row)
        if worst > threshold:
            bad = ", ".join(k for k, v in devs.items() if v > threshold)
            alerts.append(
                f"[告警] {sc['name']}: 偏差 {worst:.0%} > 阈值 {threshold:.0%} "
                f"(超差指标: {bad}) -> 解析模型在此场景不可靠, 以模拟为准")
    return rows, alerts


def main() -> None:
    ap = argparse.ArgumentParser(description="模型 vs 模拟校准")
    ap.add_argument("--n", type=int, default=50000, help="每场景模拟请求数")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="偏差告警阈值")
    args = ap.parse_args()

    rows, alerts = calibrate(n_arrivals=args.n, threshold=args.threshold)
    print(f"{'场景':<26} {'指标':<10} {'模型':>12} {'模拟':>12} {'偏差':>8}")
    for row in rows:
        m, s = row["model"], row["sim"]
        pairs = [("mean_wait", m.mean_wait, s.mean_wait),
                 ("mean_queue", m.mean_queue, s.mean_queue),
                 ("p_timeout", m.p_timeout, s.timeout_prob)]
        for i, (k, mv, sv) in enumerate(pairs):
            name = row["name"] if i == 0 else ""
            print(f"{name:<26} {k:<10} {mv:>12.4g} {sv:>12.4g} "
                  f"{row['devs'][k]:>8.1%}")
    print()
    if alerts:
        for a in alerts:
            print(a)
    else:
        print(f"所有场景偏差均在阈值 {args.threshold:.0%} 以内, 模型可信。")


if __name__ == "__main__":
    main()
