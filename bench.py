"""耗时 / 内存对比：增量维护 vs 每次查询全量重算。

默认规模：10 万顶点、100 万条初始边、1 万次增删，每 500 次增删设一个检查点，
每个检查点回答 50 个可达性查询。
  - 增量：每次增删只更新受影响部分，查询 O(1)。
  - 全量：每个检查点用 recompute_components() 从零重算后再回答查询。
每个检查点同时校验增量结果与全量重算一致（大规模对拍）。

运行：python3 bench.py [n] [m] [ops] [checkpoint_every] [queries_per_ckpt] [seed]
"""

import random
import sys
import time
import tracemalloc

from dyngraph import DynamicGraph


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    m = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    ops = int(sys.argv[3]) if len(sys.argv) > 3 else 10_000
    ckpt_every = int(sys.argv[4]) if len(sys.argv) > 4 else 500
    nq = int(sys.argv[5]) if len(sys.argv) > 5 else 50
    seed = int(sys.argv[6]) if len(sys.argv) > 6 else 12345
    random.seed(seed)

    print(f"图规模: {n} 顶点, {m} 初始边 | 增删 {ops} 次 | 每 {ckpt_every} 次一个检查点, 每点 {nq} 个查询")

    # ---- 建图（两种方式的公共起点，不计入对比） ----
    edges = set()
    while len(edges) < m:
        u, v = random.randrange(n), random.randrange(n)
        if u != v:
            edges.add((u, v) if u < v else (v, u))
    edges = list(edges)

    g = DynamicGraph()
    t0 = time.perf_counter()
    for u, v in edges:
        g.add_edge(u, v)
    t_build_incr = time.perf_counter() - t0

    t0 = time.perf_counter()
    g.recompute_components()
    t_build_full = time.perf_counter() - t0
    print(f"建图: 增量插入 {t_build_incr:.2f}s | 全量重算一次 {t_build_full:.2f}s")

    # ---- 增删 + 查询阶段 ----
    live = list(edges)
    pos = {e: i for i, e in enumerate(live)}  # 边 -> 在 live 中的下标

    def fresh_edge():
        while True:
            u, v = random.randrange(n), random.randrange(n)
            if u == v:
                continue
            e = (u, v) if u < v else (v, u)
            if e not in pos:
                return e

    def apply_delete_to_pool():
        i = random.randrange(len(live))
        e = live[i]
        last = live.pop()
        if i < len(live):
            live[i] = last
            pos[last] = i
        del pos[e]
        return e

    def apply_add_to_pool():
        e = fresh_edge()
        pos[e] = len(live)
        live.append(e)
        return e

    t_incr = 0.0
    t_full = 0.0
    t_incr_query = 0.0
    splits = 0
    merges = 0
    stats_log = []
    tracemalloc.start()
    mem0 = tracemalloc.get_traced_memory()[0]

    for step in range(1, ops + 1):
        if random.random() < 0.5 and live:
            u, v = apply_delete_to_pool()
            t0 = time.perf_counter()
            before = g.component_count()
            g.remove_edge(u, v)
            t_incr += time.perf_counter() - t0
            if g.component_count() > before:
                splits += 1

        else:
            u, v = apply_add_to_pool()
            t0 = time.perf_counter()
            before = g.component_count()
            g.add_edge(u, v)
            t_incr += time.perf_counter() - t0
            if g.component_count() < before:
                merges += 1

        if step % ckpt_every == 0:
            if nq > 0:
                # 增量：O(1) 查询
                qs = [(random.randrange(n), random.randrange(n)) for _ in range(nq)]
                t0 = time.perf_counter()
                ans_incr = [g.connected(a, b) for a, b in qs]
                t_incr_query += time.perf_counter() - t0
                # 全量：从零重算后回答
                t0 = time.perf_counter()
                labels = g.recompute_components()
                t_full += time.perf_counter() - t0
                ans_full = [labels.get(a) == labels.get(b) and a in labels for a, b in qs]
                assert ans_incr == ans_full, f"检查点 {step}: 增量与全量结果不一致"
            cur, peak = tracemalloc.get_traced_memory()
            stats_log.append((step, g._stats()["components"], g.memory_bytes(), cur - mem0, peak - mem0))

    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    n_ckpt = ops // ckpt_every
    total_q = n_ckpt * nq
    print(f"\n增删构成: 分裂 {splits} 次, 合并 {merges} 次, 其余为重数/非树边/替换边")
    print(f"一致性: {n_ckpt} 个检查点全部通过对拍（增量 == 全量）")
    print(f"\n== 耗时对比（{ops} 次增删 + {total_q} 次查询） ==")
    print(f"增量: 增删累计 {t_incr:.3f}s + 查询累计 {t_incr_query*1000:.1f}ms = {t_incr + t_incr_query:.3f}s")
    print(f"全量: 重算累计 {t_full:.3f}s（{n_ckpt} 次重算, 均值 {t_full/max(n_ckpt,1):.3f}s/次）")
    if t_incr + t_incr_query > 0:
        print(f"加速比: {t_full / (t_incr + t_incr_query):.1f}x")
    per_q = t_full / max(n_ckpt, 1)
    print(f"外推: 若每次查询都全量重算, {ops} 次增删 + {ops} 次查询约需 {ops * per_q:.0f}s, "
          f"增量约需 {t_incr + ops * (t_incr_query / max(total_q,1)):.2f}s")

    print(f"\n== 内存（增量结构, tracemalloc 相对增删阶段起点） ==")
    print(f"结束: current {cur - mem0}, peak {peak - mem0}")
    print("检查点(步, 分量数, 库结构内存, 进程current, 进程peak):")
    for s in stats_log:
        print(f"  {s[0]:>6}  {s[1]:>8}  {s[2]:>14,}  {s[3]:>14,}  {s[4]:>14,}")
    print("内部条目:", g._stats())


if __name__ == "__main__":
    main()
