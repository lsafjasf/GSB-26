"""大数据场景下比对与修复的耗时基准。

用法：python3 benchmark.py [--mb 128] [--replicas 3] [--chunk-mb 1]
"""

import argparse
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reheal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mb", type=int, default=128, help="单副本大小 MiB")
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--chunk-mb", type=int, default=1, help="块大小 MiB")
    args = parser.parse_args()

    chunk_size = args.chunk_mb << 20
    size = args.mb << 20
    tmp = tempfile.mkdtemp(prefix="reheal-bench-")
    try:
        paths = [os.path.join(tmp, f"replica{i}.bin") for i in range(args.replicas)]

        t0 = time.perf_counter()
        with open(paths[0], "wb") as fh:
            remaining = size
            while remaining > 0:
                block = os.urandom(min(remaining, 8 << 20))
                fh.write(block)
                remaining -= len(block)
        for p in paths[1:]:
            shutil.copyfile(paths[0], p)
        gen_t = time.perf_counter() - t0

        # 制造 1% 的损坏块（仅副本 0）
        n_chunks = size // chunk_size
        corrupt = max(1, n_chunks // 100)
        for i in range(0, corrupt * 7, 7):
            reheal.write_chunk(paths[0], i, os.urandom(chunk_size), chunk_size)

        t0 = time.perf_counter()
        report = reheal.scan_replicas(paths, chunk_size)
        scan_t = time.perf_counter() - t0

        t0 = time.perf_counter()
        repair = reheal.repair_replicas(paths, chunk_size)
        repair_t = time.perf_counter() - t0

        total_mb = args.mb * args.replicas
        fixed = sum(len(v) for v in repair.applied.values())
        print(f"副本数={args.replicas}  单副本={args.mb} MiB  块大小={args.chunk_mb} MiB  块数/副本={n_chunks}")
        print(f"生成数据:            {gen_t:7.2f} s")
        print(f"比对(scan):          {scan_t:7.2f} s  ({total_mb / scan_t:8.1f} MiB/s, 共 {total_mb} MiB)")
        print(f"修复(repair):        {repair_t:7.2f} s  (修复 {fixed} 个块)")
        print(f"比对+修复合计:       {scan_t + repair_t:7.2f} s")
        assert reheal.scan_replicas(paths, chunk_size).consistent
        print("修复后一致性校验:    通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
