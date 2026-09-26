"""内存峰值基准：验证“峰值内存由 chunk_size 主导，近似 O(chunk_size)”。

方法：生成 N 条定长记录，用 tracemalloc 统计排序期间 Python 堆峰值，
并记录 ru_maxrss（进程 RSS 峰值，含解释器基线）。

运行：python3 memory_benchmark.py
"""

import os
import resource
import tempfile
import tracemalloc

from external_sort import external_sort

N = 100_000
PAYLOAD = "x" * 64


def make_key(line):
    return int(line.split("\t", 1)[0])


def main():
    tmp = tempfile.mkdtemp(prefix="extsort_bench_")
    src = os.path.join(tmp, "input.txt")
    dst = os.path.join(tmp, "output.txt")
    with open(src, "w", encoding="utf-8") as f:
        for i in range(N):
            # 键逆序写入，确保每块都需要真实排序
            f.write("%08d\t%s%08d\n" % (N - i, PAYLOAD, i))
    rec_bytes = os.path.getsize(src) // N
    print("records=%d, record_size=%dB, total=%.1fMB"
          % (N, rec_bytes, os.path.getsize(src) / 1e6))
    print("%-10s %8s %14s %14s" % ("chunk_size", "num_runs",
                                   "py_peak(MB)", "maxrss(MB)"))
    for chunk_size in (1_000, 5_000, 20_000, 50_000, 100_000):
        tracemalloc.start()
        external_sort(src, dst, make_key, chunk_size=chunk_size, temp_dir=tmp)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        num_runs = -(-N // chunk_size)
        print("%-10d %8d %14.1f %14.1f" % (chunk_size, num_runs,
                                           peak / 1e6, rss))
    os.unlink(src)
    os.unlink(dst)
    os.rmdir(tmp)


if __name__ == "__main__":
    main()
