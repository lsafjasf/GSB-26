"""内存峰值基准：峰值由「块驻留」与「归并路数」两项中的较大者决定。

峰值内存 ≈ max(chunk_size × 单条记录驻留开销,
               归并路数(= 输入条数 / chunk_size) × 单读取器开销)

因此峰值并非单纯随 chunk_size 增长：块过小时归并路数膨胀，峰值不降反升
（实验 A 第一行）；也只有在块驻留占主导时，峰值才与输入总量无关（实验 B）。

用法: python3 tests/memory_benchmark.py
"""

import os
import shutil
import sys
import tempfile
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from external_sort import external_sort

RECORD_BYTES = 64  # 每条记录约 64 字节


def generate(path, n_records, seed=7):
    # 伪随机键（LCG），保证多轮生成内容一致且无需加载进内存。
    x = seed
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_records):
            x = (x * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
            key = "k%06d" % (x % 100000)
            payload = "v" * (RECORD_BYTES - len(key) - 2)
            f.write("%s\t%s\n" % (key, payload))


def measure(input_path, output_path, chunk_size, temp_dir):
    tracemalloc.start()
    external_sort(input_path, output_path, chunk_size=chunk_size, temp_dir=temp_dir)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def main():
    work = tempfile.mkdtemp(prefix="extsort-bench-")
    try:
        input_path = os.path.join(work, "input.txt")
        output_path = os.path.join(work, "output.txt")

        print("记录大小 ~%d 字节\n" % RECORD_BYTES)

        print("实验 A：输入固定 200,000 条，块大小变化"
              "（块过小时归并路数膨胀，峰值不降反升）")
        print("%12s %14s" % ("chunk_size", "peak MiB"))
        generate(input_path, 200_000)
        for chunk_size in (1_000, 10_000, 50_000, 200_000):
            peak = measure(input_path, output_path, chunk_size, work)
            print("%12d %14.2f" % (chunk_size, peak / 2**20))

        print("\n实验 B：块大小固定 10,000，输入总量变化"
              "（块驻留占主导，峰值基本恒定）")
        print("%12s %14s" % ("n_records", "peak MiB"))
        for n in (50_000, 200_000, 800_000):
            generate(input_path, n)
            peak = measure(input_path, output_path, 10_000, work)
            print("%12d %14.2f" % (n, peak / 2**20))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
