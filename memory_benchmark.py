"""峰值内存二维基准：chunk_size（块大小）× merge_ways（归并路数）。

方法：
  - 生成 N 条定长记录（逆序键，保证每块真实排序）；
  - 对每个 (chunk_size, merge_ways) 组合：
      * 同进程内用 tracemalloc 统计排序期间 Python 堆峰值（py_peak）；
      * 隔离子进程用 resource.ru_maxrss 统计该配置的进程级 RSS 峰值
        （maxrss 单调不减，必须逐格隔离子进程，否则后跑的配置会
         继承前面的峰值，污染二维对比）；
  - 输出二维矩阵（行=chunk_size，列=merge_ways）与明细 CSV。

内存模型（与实现一致）：
  峰值 ≈ chunk_size 条记录的分块缓冲区
       + merge_ways 条记录的归并堆与读缓冲（每路一个打开的 run 文件）
  即 O(chunk_size + merge_ways)；多轮归并不改变该上界。

运行：
  python3 memory_benchmark.py                 # 默认 10 万条
  python3 memory_benchmark.py --n 200000      # 自定义规模
  python3 memory_benchmark.py --csv out.csv   # 指定 CSV 路径
"""

import argparse
import csv
import multiprocessing as mp
import os
import resource
import sys
import tempfile
import tracemalloc

from external_sort import external_sort

CHUNK_SIZES = (1_000, 5_000, 20_000, 50_000, 100_000)
MERGE_WAYS = (2, 4, 8, 16, 32)
PAYLOAD = "x" * 64


def make_key(line):
    return int(line.split("\t", 1)[0])


def make_input(src, n):
    with open(src, "w", encoding="utf-8") as f:
        for i in range(n):
            # 键逆序写入，确保每块都需要真实排序
            f.write("%08d\t%s%08d\n" % (n - i, PAYLOAD, i))


def _merge_rounds(num_runs, ways):
    rounds, cur = 0, num_runs
    while cur > ways:
        cur = -(-cur // ways)
        rounds += 1
    if num_runs > 1:
        rounds += 1
    return rounds


def measure_py_peak(src, dst, chunk_size, merge_ways):
    """同进程 tracemalloc 峰值（Python 分配，可区分本次排序）。"""
    tracemalloc.start()
    external_sort(src, dst, make_key, chunk_size=chunk_size,
                  merge_ways=merge_ways, temp_dir=os.path.dirname(src))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1e6


def _child_maxrss(queue, src, dst, chunk_size, merge_ways):
    external_sort(src, dst, make_key, chunk_size=chunk_size,
                  merge_ways=merge_ways, temp_dir=os.path.dirname(src))
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    queue.put(rss_mb)


def measure_maxrss_isolated(src, dst, chunk_size, merge_ways):
    """隔离子进程的 RSS 峰值：消除 maxrss 单调累积的跨配置污染。"""
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child_maxrss,
                       args=(queue, src, dst, chunk_size, merge_ways))
    proc.start()
    rss_mb = queue.get()
    proc.join()
    if proc.exitcode != 0:
        raise RuntimeError("child benchmark failed (exit=%d)" % proc.exitcode)
    return rss_mb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--csv", default="memory_peak_2d.csv")
    args = ap.parse_args()
    n = args.n

    tmp = tempfile.mkdtemp(prefix="extsort_bench_")
    src = os.path.join(tmp, "input.txt")
    dst = os.path.join(tmp, "output.txt")
    make_input(src, n)
    rec_bytes = os.path.getsize(src) // n

    rows = []
    for chunk_size in CHUNK_SIZES:
        for merge_ways in MERGE_WAYS:
            py_peak = measure_py_peak(src, dst, chunk_size, merge_ways)
            maxrss = measure_maxrss_isolated(src, dst, chunk_size, merge_ways)
            num_runs = -(-n // chunk_size)
            rows.append({
                "chunk_size": chunk_size,
                "merge_ways": merge_ways,
                "num_runs": num_runs,
                "merge_rounds": _merge_rounds(num_runs, merge_ways),
                "py_peak_mb": round(py_peak, 3),
                "maxrss_mb": round(maxrss, 1),
            })

    print("records=%d, record_size=%dB, total=%.1fMB"
          % (n, rec_bytes, os.path.getsize(src) / 1e6))
    print("py_peak: tracemalloc 统计的排序期间 Python 堆峰值（MB）")
    print("maxrss : 隔离子进程 ru_maxrss（MB，含解释器基线）")
    print()

    header = "chunk_size | runs |" + "".join(
        " ways=%-4d" % w for w in MERGE_WAYS)
    print("py_peak (MB)\n" + header)
    print("-" * len(header))
    for chunk_size in CHUNK_SIZES:
        cells = [r for r in rows if r["chunk_size"] == chunk_size]
        line = "%10d | %4d |" % (chunk_size, cells[0]["num_runs"])
        line += "".join(" %8.3f" % r["py_peak_mb"] for r in cells)
        print(line)
    print()
    print("maxrss (MB, 隔离子进程)\n" + header)
    print("-" * len(header))
    for chunk_size in CHUNK_SIZES:
        cells = [r for r in rows if r["chunk_size"] == chunk_size]
        line = "%10d | %4d |" % (chunk_size, cells[0]["num_runs"])
        line += "".join(" %8.1f" % r["maxrss_mb"] for r in cells)
        print(line)
    print()
    print("merge_rounds（归并轮数）\n" + header)
    print("-" * len(header))
    for chunk_size in CHUNK_SIZES:
        cells = [r for r in rows if r["chunk_size"] == chunk_size]
        line = "%10d | %4d |" % (chunk_size, cells[0]["num_runs"])
        line += "".join(" %8d" % r["merge_rounds"] for r in cells)
        print(line)

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("\n明细已写入 %s" % args.csv)


if __name__ == "__main__":
    sys.exit(main())
