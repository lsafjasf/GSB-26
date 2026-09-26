"""性能基准：大数据量下比对与修复耗时。用法: python3 benchmark.py [MiB] [副本数]"""

import sys
import time

from replica_sync import (
    DEFAULT_CHUNK_SIZE, ReplicaStore, compare_replicas, repair_replicas,
)


def main():
    size_mib = int(sys.argv[1]) if len(sys.argv) > 1 else 256
    n_replicas = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    chunk = DEFAULT_CHUNK_SIZE
    n_chunks = (size_mib * (1 << 20)) // chunk

    print(f"数据规模: {size_mib} MiB/副本 x {n_replicas} 副本, "
          f"块大小 {chunk // (1 << 20)} MiB, 共 {n_chunks} 块/副本")

    t0 = time.perf_counter()
    seed = bytes(range(256)) * (chunk // 256)
    replicas = [ReplicaStore(f"R{i+1}") for i in range(n_replicas)]
    for r in replicas:
        for i in range(n_chunks):
            r.write_chunk(i, seed[:chunk - 8] + i.to_bytes(8, "big"))
    t1 = time.perf_counter()
    print(f"构造数据: {t1 - t0:.2f} s")

    # 1) 全员一致时的比对耗时
    t0 = time.perf_counter()
    report = compare_replicas(replicas)
    t1 = time.perf_counter()
    total = size_mib * n_replicas
    assert report.clean
    print(f"比对(全一致): {t1 - t0:.2f} s  ({total / (t1 - t0):.0f} MiB/s, "
          f"{n_chunks * n_replicas / (t1 - t0):.0f} 块/s)")

    # 2) 注入坏块后比对 + 修复（不同副本的坏块错开，保证多数派存在）
    for i in range(0, n_chunks, 20):
        replicas[1].corrupt_chunk(i, b"rot" * chunk)      # R2 位腐烂
    for i in range(10, n_chunks, 20):
        replicas[2 % n_replicas].delete_chunk(i)          # R3 缺块

    t0 = time.perf_counter()
    report = compare_replicas(replicas)
    t1 = time.perf_counter()
    n_issues = sum(len(v) for v in report.diffs.values())
    print(f"比对(含坏块): {t1 - t0:.2f} s，检出 {n_issues} 处差异")

    t0 = time.perf_counter()
    repair = repair_replicas(replicas, report)
    t1 = time.perf_counter()
    fixed_bytes = len(repair.repaired) * chunk
    print(f"修复: {t1 - t0:.2f} s，修复 {len(repair.repaired)} 块 "
          f"({fixed_bytes / (1 << 20):.0f} MiB, "
          f"{fixed_bytes / (1 << 20) / max(t1 - t0, 1e-9):.0f} MiB/s)")

    t0 = time.perf_counter()
    assert compare_replicas(replicas).clean
    t1 = time.perf_counter()
    print(f"修复后复验: {t1 - t0:.2f} s，全部一致")


if __name__ == "__main__":
    main()
