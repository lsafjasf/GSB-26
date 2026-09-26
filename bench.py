"""blockzip 基准：随机读 vs 顺序读耗时、块大小与随机读延迟关系、解压量统计。

运行: python3 bench.py
"""

import random
import time
import zlib

from blockzip import BlockReader, ReadStats, compress

DATA_SIZE = 64 * 1024 * 1024   # 64 MiB
N_RANDOM = 2000
READ_LEN = 4096


def make_data(n: int, seed: int = 2026) -> bytes:
    rng = random.Random(seed)
    parts = []
    while n > 0:
        k = min(n, 1 << 20)
        if rng.random() < 0.5:
            parts.append(rng.randbytes(k))
        else:
            word = rng.randbytes(rng.randint(8, 256))
            parts.append((word * (k // len(word) + 1))[:k])
        n -= k
    return b"".join(parts)


def bench_random_vs_sequential(data: bytes, block_size: int) -> None:
    blob = compress(data, block_size)
    rng = random.Random(1)

    # 小随机读
    stats = ReadStats()
    r = BlockReader(blob, stats=stats)
    t0 = time.perf_counter()
    for _ in range(N_RANDOM):
        off = rng.randrange(0, len(data) - READ_LEN)
        r.read(off, READ_LEN)
    t_rand = time.perf_counter() - t0

    # 大顺序读（整文件）
    stats2 = ReadStats()
    r2 = BlockReader(blob, stats=stats2)
    t0 = time.perf_counter()
    got = r2.read(0, len(data))
    t_seq = time.perf_counter() - t0
    assert got == data

    # 对照：每次随机读都整文件解压（朴素做法）
    t0 = time.perf_counter()
    for _ in range(50):
        off = rng.randrange(0, len(data) - READ_LEN)
        zlib.decompress(blob[29:])  # 示意性整解压，仅取 50 次避免太久
    t_naive = (time.perf_counter() - t0) / 50 * N_RANDOM

    total = len(data)
    print(f"== 随机读 vs 顺序读 (数据 {total/2**20:.0f} MiB, 块 {block_size//1024} KiB, "
          f"{N_RANDOM} 次 x {READ_LEN} B 随机读) ==")
    print(f"  小随机读: {t_rand*1e3:8.1f} ms  平均 {t_rand/N_RANDOM*1e6:8.1f} us/次")
    print(f"    统计: {stats.summary()}")
    print(f"    每次平均解压 {stats.bytes_decompressed/stats.reads:8.0f} B "
          f"({stats.blocks_decompressed/stats.reads:.2f} 块), "
          f"仅为全量 {total} B 的 {stats.bytes_decompressed/stats.reads/total:.4%}")
    print(f"  大顺序读: {t_seq*1e3:8.1f} ms  ({total/t_seq/2**20:.0f} MiB/s)")
    print(f"    统计: {stats2.summary()}")
    print(f"  朴素整解压对照(估算 {N_RANDOM} 次): {t_naive*1e3:8.1f} ms "
          f"-> 分块随机读快约 {t_naive/t_rand:.0f}x")
    print()


def bench_block_size(data: bytes) -> None:
    print(f"== 块大小 vs 随机读延迟 (数据 {len(data)/2**20:.0f} MiB, "
          f"{N_RANDOM} 次 x {READ_LEN} B 随机读) ==")
    print(f"  {'块大小':>10} {'压缩率':>8} {'总耗时ms':>10} {'us/次':>9} "
          f"{'平均解压B/次':>12} {'平均块/次':>9}")
    rng = random.Random(1)
    offs = [rng.randrange(0, len(data) - READ_LEN) for _ in range(N_RANDOM)]
    for bs in (4 * 1024, 16 * 1024, 64 * 1024, 256 * 1024, 1024 * 1024, 4 * 1024 * 1024):
        blob = compress(data, bs)
        stats = ReadStats()
        r = BlockReader(blob, stats=stats)
        t0 = time.perf_counter()
        for off in offs:
            r.read(off, READ_LEN)
        dt = time.perf_counter() - t0
        print(f"  {bs//1024:>8} KiB {len(blob)/len(data):>8.2%} {dt*1e3:>10.1f} "
              f"{dt/N_RANDOM*1e6:>9.1f} {stats.bytes_decompressed/stats.reads:>12.0f} "
              f"{stats.blocks_decompressed/stats.reads:>9.2f}")
    print()


def main() -> None:
    print(f"生成 {DATA_SIZE/2**20:.0f} MiB 测试数据 ...")
    data = make_data(DATA_SIZE)
    bench_random_vs_sequential(data, 64 * 1024)
    bench_block_size(data)


if __name__ == "__main__":
    main()
