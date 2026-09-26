"""规模基准：python3 benchmark.py [函数总数，默认 100000]

生成合成模块：每 100 个函数构成一个环（块内 fN -> fN+1 循环），
块首再向下一快发一条前向边（不合并 SCC）。总计 1000 个长度为 100 的环。
测量：源码解析、调用提取、图构建、环检测的耗时与内存（tracemalloc 峰值 + RSS）。
另测单函数增量更新耗时，与全量环检测对比。
"""

import gc
import resource
import sys
import time
import tracemalloc

from callgraph import CallGraph, IncrementalEngine, find_cycles, verify_cycle
from callgraph.parser import parse_module


def gen_source(total: int, block: int = 100) -> str:
    parts = []
    nblocks = total // block
    for b in range(nblocks):
        base = b * block
        for i in range(block):
            nxt = base + (i + 1) % block
            lines = [f"def f{base + i}():", f"    f{nxt}()"]
            if i == 0 and b + 1 < nblocks:
                lines.append(f"    f{base + block}()")  # 跨块前向边
            parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> None:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    print(f"生成 {total} 个函数的合成源码 ...")
    t0 = time.perf_counter()
    src = gen_source(total)
    t_gen = time.perf_counter() - t0
    print(f"源码大小: {len(src) / 1e6:.2f} MB，生成耗时 {t_gen:.2f}s")

    tracemalloc.start()
    t0 = time.perf_counter()
    model = parse_module(src)          # 含 ast.parse + 定义收集 + 调用提取
    t_parse = time.perf_counter() - t0
    cur, peak_parse = tracemalloc.get_traced_memory()
    print(f"[1] 解析+提取调用: {t_parse:.2f}s | tracemalloc 峰值 {peak_parse / 1e6:.1f} MB")

    t0 = time.perf_counter()
    graph = CallGraph.from_model(model)
    t_build = time.perf_counter() - t0
    print(f"[2] 图构建: {t_build:.2f}s")

    t0 = time.perf_counter()
    cycles = find_cycles(graph)
    t_cycles = time.perf_counter() - t0
    cur, peak_all = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"[3] 环检测: {t_cycles:.2f}s | 检出 {len(cycles)} 个环")
    assert all(verify_cycle(graph, c) for c in cycles)

    del model, src
    gc.collect()
    st = graph.stats()
    print()
    print("=== 规模数据 ===")
    print(f"函数数:            {total}")
    print(f"图节点/边:         {st['nodes']} / {st['edges']}")
    print(f"环数量:            {len(cycles)}（长度均为 {cycles[0].length if cycles else '-'}）")
    print(f"解析+提取:         {t_parse:.2f}s ({total / t_parse:,.0f} 函数/秒)")
    print(f"图构建:            {t_build:.2f}s")
    print(f"环检测:            {t_cycles:.2f}s")
    print(f"合计:              {t_parse + t_build + t_cycles:.2f}s")
    print(f"构建期内存峰值:    {peak_all / 1e6:.1f} MB (tracemalloc)")
    print(f"进程峰值 RSS:      {rss_mb():.1f} MB")
    print(f"平均每函数耗时:    {(t_parse + t_build + t_cycles) / total * 1e6:.1f} µs")

    # ---- 增量更新基准 ----
    print()
    print("=== 增量更新（单函数改动）===")
    eng = IncrementalEngine(graph)
    mid = total // 2
    t0 = time.perf_counter()
    rep = eng.update_calls(f"f{mid}", [(f"f{mid + 1}", True, "direct")])
    t_inc = time.perf_counter() - t0
    print(f"单函数出边替换 + 受影响子图({rep.region_size} 节点)环重扫: {t_inc * 1e3:.2f} ms")
    t0 = time.perf_counter()
    full = find_cycles(graph)
    t_full = time.perf_counter() - t0
    print(f"对照：全量环检测重跑: {t_full:.2f}s")
    same = sorted(c.path for c in eng.cycles) == sorted(c.path for c in full)
    print(f"增量结果与全量重建一致: {same}")
    assert same


if __name__ == "__main__":
    main()
