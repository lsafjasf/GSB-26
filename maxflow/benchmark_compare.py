"""benchmark_compare.py — Dinic vs 推送-重标号：三类网络对比 + 对拍。

输入类别（图生成器复用 benchmark.py，同种子可复算）：
1. 稠密随机网络   E ~ 0.5 * V^2
2. 随机稀疏网络   E ~ 4V
3. 分层对抗网络   层间全连接（本库已知最差结构，必须包含）

每个用例对两种算法各测 repeat 次取最优，输出耗时与
相位数（Dinic）/ 推送与重标号次数（推送-重标号），
并校验两者流值一致（对拍），不一致即断言失败。

用法: python3 benchmark_compare.py          # 完整规模
      python3 benchmark_compare.py quick    # 快速自检规模
"""

import sys
import time

from maxflow import ALGORITHMS, make_maxflow
from benchmark import sparse_random, dense_random, layered_hard


def run_case(name, n, edges, s, t, repeat=3):
    flows = {}
    cols = {}
    for algo in ALGORITHMS:
        best = None
        stats = None
        for _ in range(repeat):
            mf = make_maxflow(n, algo)
            for u, v, c in edges:
                mf.add_edge(u, v, c)
            t0 = time.perf_counter()
            flow = mf.max_flow(s, t)
            solve = time.perf_counter() - t0
            if best is None or solve < best:
                best = solve
                stats = dict(mf.stats)
        flows[algo] = flow
        cols[algo] = (best, stats)
    assert flows["dinic"] == flows["push_relabel"], \
        f"{name}: 对拍失败 dinic={flows['dinic']} pr={flows['push_relabel']}"
    dt, ds = cols["dinic"]
    pt, ps = cols["push_relabel"]
    print(f"{name:<22} V={n:<7} E={len(edges):<9} flow={flows['dinic']:<13}"
          f"| dinic {dt*1e3:9.1f}ms phases={ds['phases']:<4}"
          f"| push_relabel {pt*1e3:9.1f}ms "
          f"pushes={ps['pushes']:<9} relabels={ps['relabels']}",
          flush=True)


def main(quick):
    if quick:
        dense_ns, sparse_ns = (200, 400), (5_000, 20_000)
        layered = ((10, 60),)
    else:
        dense_ns, sparse_ns = (300, 700, 1200), (10_000, 50_000, 100_000)
        layered = ((20, 100), (10, 200))

    print("== 稠密随机网络 (E ~ 0.5*V^2, seed=2) ==")
    for n in dense_ns:
        run_case(f"dense n={n}", n, dense_random(n, 0.5, seed=2), 0, n - 1)

    print("\n== 随机稀疏网络 (E ~ 4V, seed=1) ==")
    for n in sparse_ns:
        run_case(f"sparse n={n}", n, sparse_random(n, 4, seed=1), 0, n - 1)

    print("\n== 分层对抗网络（最差结构, seed=5）==")
    for layers, w in layered:
        edges, s, t = layered_hard(layers, w, seed=5)
        run_case(f"layered L={layers} w={w}", layers * w + 2, edges, s, t)

    print("\nPASS: 全部用例两种算法流值一致（对拍通过）")


if __name__ == "__main__":
    main(quick=len(sys.argv) > 1 and sys.argv[1] == "quick")
