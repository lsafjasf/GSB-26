"""仿真与基准：公平性偏差、饥饿上界验证、空队列重分配、吞吐测量。

运行：python3 simulation.py
"""

import time

from fair_scheduler import WeightedFairScheduler


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def fairness_report(weights, n_dequeues):
    """各队列持续积压，统计调度次数与权重比例的偏差。"""
    section(f"公平性偏差  weights={weights}  出队次数={n_dequeues}")
    sched = WeightedFairScheduler()
    for name, w in weights.items():
        sched.add_queue(name, w)
    # 始终保持积压：边取边补
    for name in weights:
        for i in range(n_dequeues):
            sched.enqueue(name, (name, i))
    counts = {n_: 0 for n_ in weights}
    for _ in range(n_dequeues):
        item = sched.dequeue()
        counts[item[0]] += 1
        sched.enqueue(item[0], item)  # 立即补充，维持积压
    W = sum(weights.values())
    print(f"{'队列':<8}{'权重':>6}{'实际次数':>12}{'期望次数':>14}"
          f"{'实际占比':>10}{'期望占比':>10}{'|偏差|':>10}{'偏差上界':>10}")
    max_dev = 0.0
    for name, w in weights.items():
        expected = n_dequeues * w / W
        bound = w * (1 - w / W)
        dev = abs(counts[name] - expected)
        max_dev = max(max_dev, dev)
        print(f"{name:<8}{w:>6}{counts[name]:>12}{expected:>14.1f}"
              f"{counts[name]/n_dequeues:>10.4f}{w/W:>10.4f}"
              f"{dev:>10.2f}{bound:>10.2f}")
    ok = all(abs(counts[n_] - n_dequeues * w / W) <= w * (1 - w / W) + 1e-9
             for n_, w in weights.items())
    print(f"结论：所有队列偏差均在上界 w_i*(1-w_i/W) 之内 -> {ok}")


def starvation_report(weights, n_dequeues):
    """统计每个队列相邻两次被调度的最大间隔，验证饥饿上界。"""
    section(f"饥饿上界验证  weights={weights}  出队次数={n_dequeues}")
    sched = WeightedFairScheduler()
    for name, w in weights.items():
        sched.add_queue(name, w)
        for i in range(n_dequeues):
            sched.enqueue(name, (name, i))
    W = sum(weights.values())
    last_seen = {}
    max_gap = {n_: 0 for n_ in weights}
    for step in range(n_dequeues):
        item = sched.dequeue()
        sched.enqueue(item[0], item)
        name = item[0]
        if name in last_seen:
            max_gap[name] = max(max_gap[name], step - last_seen[name] - 1)
        last_seen[name] = step
    print(f"{'队列':<8}{'权重':>6}{'最大间隔(次)':>14}{'上界 (W-w_i)*Q':>16}{'满足':>8}")
    for name, w in weights.items():
        bound = W - w
        print(f"{name:<8}{w:>6}{max_gap[name]:>14}{bound:>16}"
              f"{str(max_gap[name] <= bound):>8}")
    print("含义：任一有任务的队列 i，每 (W-w_i)*Q+1 次出队内必被调度至少一次，"
          "W 为活跃权重之和，Q 为 quantum。")


def redistribution_report():
    section("空队列配额重分配  weights={a:1, b:1, c:1}，c 始终为空")
    sched = WeightedFairScheduler()
    for name in ("a", "b", "c"):
        sched.add_queue(name, 1)
    for i in range(6000):
        sched.enqueue("a", ("a", i))
        sched.enqueue("b", ("b", i))
    counts = {"a": 0, "b": 0}
    for _ in range(6000):
        counts[sched.dequeue()[0]] += 1
    total = sum(counts.values())
    for name, c in counts.items():
        print(f"  {name}: {c} 次, 占比 {c/total:.4f} (重分配后期望 0.5000, "
              f"若空转耗配额则为 0.3333)")


def throughput_report():
    section("高负载吞吐基准")
    scenarios = [
        ("4 队列 等权", {"q%d" % i: 1 for i in range(4)}),
        ("8 队列 权重 1..8", {"q%d" % i: i + 1 for i in range(8)}),
        ("64 队列 等权", {"q%d" % i: 1 for i in range(64)}),
        ("2 队列 极端权重 1000:1", {"big": 1000, "small": 1}),
    ]
    n = 1_000_000
    for label, weights in scenarios:
        sched = WeightedFairScheduler()
        for name, w in weights.items():
            sched.add_queue(name, w)
        names = list(weights)
        t0 = time.perf_counter()
        for i in range(n):
            sched.enqueue(names[i % len(names)], i)
        t1 = time.perf_counter()
        taken = 0
        while sched.dequeue() is not None:
            taken += 1
        t2 = time.perf_counter()
        print(f"  {label:<24} enqueue {n/1e6/(t1-t0):>6.2f} M ops/s, "
              f"dequeue {taken/1e6/(t2-t1):>6.2f} M ops/s "
              f"(出队 {taken} 个, {t2-t1:.3f}s)")


if __name__ == "__main__":
    fairness_report({"a": 5, "b": 3, "c": 2, "d": 1}, 1_000_000)
    fairness_report({"x": 100, "y": 10, "z": 1}, 1_000_000)
    starvation_report({"a": 5, "b": 3, "c": 2, "d": 1}, 1_000_000)
    starvation_report({"big": 1000, "small": 1}, 200_000)
    redistribution_report()
    throughput_report()
