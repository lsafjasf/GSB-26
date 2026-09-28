"""性能基准：百万节点、千万边图上的 DFS 耗时与内存。

运行：python3 bench.py
"""
import random
import resource
import time
import tracemalloc

from dfs_fixed import Graph, DFS

N = 1_000_000        # 节点数
M = 10_000_000       # 总边数（含并行边）
DUP = 500_000        # 显式并行边数量


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    t0 = time.perf_counter()
    base_rss = rss_mb()

    g = Graph()
    # 链式主干：保证全图连通且存在超深路径
    for i in range(N - 1):
        g.add_edge(i, i + 1)
    # 随机边（含环）
    rng = random.Random(42)
    for _ in range(M - (N - 1) - DUP):
        g.add_edge(rng.randrange(N), rng.randrange(N))
    # 显式并行边：每条唯一边原样复制一次
    for _ in range(DUP // 2):
        u = rng.randrange(N)
        v = rng.randrange(N)
        g.add_edge(u, v)
        g.add_edge(u, v)
    t1 = time.perf_counter()
    build_rss = rss_mb()

    tracemalloc.start()
    dfs = DFS(g)
    order = dfs.traverse(0)
    t2 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    end_rss = rss_mb()

    visited = len(order)
    unique_edges = g.unique_edge_count()
    assert visited == len(set(order)), "节点重复访问"
    # 链式主干保证全图从 0 可达，故可达唯一边数 == 全图唯一边数
    assert dfs.edges_processed == unique_edges, "边处理次数不等于可达唯一边数"

    print(f"节点数:                {N:,}")
    print(f"总边数(含并行边):      {M:,}")
    print(f"唯一有向边数:          {unique_edges:,}")
    print(f"访问节点数:            {visited:,}")
    print(f"边处理次数:            {dfs.edges_processed:,}")
    print(f"建图耗时:              {t1 - t0:.2f} s")
    print(f"遍历耗时:              {t2 - t1:.2f} s")
    print(f"遍历吞吐:              {dfs.edges_processed / (t2 - t1) / 1e6:.2f} M唯一边/s")
    print(f"进程 RSS(基线):        {base_rss:.0f} MB")
    print(f"进程 RSS(建图后):      {build_rss:.0f} MB")
    print(f"进程 RSS(遍历后峰值):  {end_rss:.0f} MB")
    print(f"遍历 Python 堆峰值:    {peak / 1024 / 1024:.0f} MB (tracemalloc)")


if __name__ == "__main__":
    main()
