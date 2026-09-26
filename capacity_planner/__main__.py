"""CLI：estimate / simulate / calibrate / recommend / bench / demo"""
from __future__ import annotations

import argparse
import sys
import time

from .models import mgc_metrics, p_wait_exceeds, ModelError
from .simulate import SimConfig, run_simulation
from .calibrate import calibrate
from .recommend import recommend


def _add_common(p):
    p.add_argument("--lam", type=float, required=True, help="到达率 (请求/秒)")
    p.add_argument("--service", type=float, required=True, help="平均处理时长 (秒)")
    p.add_argument("--c", type=int, default=8, help="并发线程数")
    p.add_argument("--scv", type=float, default=1.0,
                   help="服务时间平方变异系数 (指数=1, 长尾>1)")
    p.add_argument("--timeout", type=float, default=1.0, help="排队超时 (秒)")


def cmd_estimate(args):
    m = mgc_metrics(args.lam, args.service, args.scv, args.c)
    if m.unstable:
        print(f"rho={m.rho:.2f} >= 1：系统不稳定，队列无界增长，模型无稳态解")
        return 2
    p_to = p_wait_exceeds(m, args.c, 1.0 / args.service, args.timeout)
    print(f"利用率 rho        : {m.rho:.3f}")
    print(f"排队概率 P(wait>0): {m.p_wait:.3f}")
    print(f"平均排队长度 Lq   : {m.lq:.3f}")
    print(f"平均排队等待 Wq   : {m.wq*1000:.2f} ms")
    print(f"平均逗留时间 W    : {m.w*1000:.2f} ms")
    print(f"P(等待>{args.timeout}s) : {p_to:.4%}")
    for n in m.notes:
        print(f"[说明] {n}")
    return 0


def cmd_simulate(args):
    dist = "lognormal" if args.scv != 1.0 else "exponential"
    cfg = SimConfig(lam=args.lam, c=args.c, mean_service=args.service,
                    dist=dist, scv_service=args.scv, timeout=args.timeout,
                    sim_time=args.sim_time, seed=args.seed)
    r = run_simulation(cfg)
    print(f"到达 {r.arrived}  服务 {r.served}  超时 {r.timed_out}  丢弃 {r.dropped}")
    print(f"平均等待 {r.mean_wait*1000:.2f} ms   p95 等待 {r.p95_wait*1000:.2f} ms")
    print(f"时间平均队列 {r.mean_queue:.3f}   峰值队列 {r.max_queue}")
    print(f"利用率 {r.utilization:.3f}   吞吐 {r.throughput:.2f}/s")
    print(f"P(超时) {r.p_timeout:.4%}")
    for n in r.notes:
        print(f"[说明] {n}")
    return 0


def cmd_calibrate(args):
    dist = "lognormal" if args.scv != 1.0 else "exponential"
    cfg = SimConfig(lam=args.lam, c=args.c, mean_service=args.service,
                    dist=dist, scv_service=args.scv, timeout=args.timeout,
                    sim_time=args.sim_time, seed=args.seed)
    rep = calibrate(cfg, threshold=args.threshold)
    print(rep.format())
    return 0 if rep.ok else 1


def cmd_recommend(args):
    burst = None
    if args.burst_rate and args.burst_duration:
        burst = (args.burst_duration, args.burst_rate)
    rec = recommend(lam=args.lam, mean_service=args.service,
                    scv_service=args.scv, timeout=args.timeout,
                    target_p_timeout=args.target_ptimeout,
                    target_rho=args.target_rho,
                    mem_per_active_mb=args.mem_active,
                    mem_per_queued_mb=args.mem_queued,
                    burst=burst)
    print(rec.format())
    return 0 if rec.feasible else 2


def cmd_bench(args):
    """不同参数组合的估算耗时。"""
    combos = [
        ("低负载 指数服务", 10, 0.05, 1.0, 4),
        ("中负载 指数服务", 100, 0.05, 1.0, 8),
        ("高负载 rho~0.9", 500, 0.02, 1.0, 12),
        ("长尾 SCV=3", 200, 0.05, 3.0, 16),
        ("大并发 c=256", 2000, 0.1, 1.0, 256),
        ("单线程 c=1", 5, 0.1, 1.0, 1),
    ]
    print(f"{'场景':<22}{'lam':>7}{'service':>9}{'SCV':>5}{'c':>5}"
          f"{'Wq(ms)':>10}{'耗时(us)':>10}")
    for name, lam, svc, scv, c in combos:
        t0 = time.perf_counter()
        m = mgc_metrics(lam, svc, scv, c)
        p_wait_exceeds(m, c, 1.0 / svc, 1.0)
        dt = (time.perf_counter() - t0) * 1e6
        wq = "inf" if m.unstable else f"{m.wq*1000:.2f}"
        print(f"{name:<22}{lam:>7}{svc:>9}{scv:>5}{c:>5}{wq:>10}{dt:>10.1f}")
    # 模拟耗时
    print("\n模拟耗时（sim_time=2000s, 指数服务）:")
    print(f"{'lam':>7}{'c':>5}{'事件数(约)':>14}{'耗时(ms)':>12}")
    for lam, c in [(10, 4), (100, 8), (500, 12), (2000, 64)]:
        cfg = SimConfig(lam=lam, c=c, mean_service=0.05, sim_time=2000.0)
        t0 = time.perf_counter()
        r = run_simulation(cfg)
        dt = (time.perf_counter() - t0) * 1e3
        print(f"{lam:>7}{c:>5}{r.arrived:>14}{dt:>12.1f}")
    return 0


def cmd_demo(args):
    print("== 1. 配额建议样例 ==")
    rec = recommend(lam=120, mean_service=0.05, scv_service=1.5, timeout=0.5,
                    burst=(10.0, 400))
    print(rec.format())
    print("\n== 2. 模拟校准 ==")
    cfg = SimConfig(lam=120, c=10, mean_service=0.05, dist="lognormal",
                    scv_service=1.5, timeout=0.5, sim_time=2000.0)
    rep = calibrate(cfg)
    print(rep.format())
    print("\n== 3. 估算耗时 ==")
    return cmd_bench(args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="capacity_planner",
                                 description="资源容量规划计算库 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("estimate", help="解析模型估算")
    _add_common(p)
    p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("simulate", help="运行离散事件模拟")
    _add_common(p)
    p.add_argument("--sim-time", type=float, default=2000.0)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("calibrate", help="模型 vs 模拟校准")
    _add_common(p)
    p.add_argument("--sim-time", type=float, default=2000.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threshold", type=float, default=0.15)
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("recommend", help="输出配额建议")
    _add_common(p)
    p.add_argument("--target-rho", type=float, default=0.7)
    p.add_argument("--target-ptimeout", type=float, default=0.01)
    p.add_argument("--mem-active", type=float, default=1.0, help="MB/活跃请求")
    p.add_argument("--mem-queued", type=float, default=0.05, help="MB/排队请求")
    p.add_argument("--burst-rate", type=float, default=0.0)
    p.add_argument("--burst-duration", type=float, default=0.0)
    p.set_defaults(fn=cmd_recommend)

    p = sub.add_parser("bench", help="不同参数组合的估算耗时")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("demo", help="建议+校准+耗时 全演示")
    p.set_defaults(fn=cmd_demo)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ModelError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
