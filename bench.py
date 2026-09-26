"""基准：随机小读 vs 大顺序读；块大小与随机读延迟的关系。"""

import random
import time

from blockzip import Reader, compress
from test_blockzip import make_data

SIZE = 64 * 1024 * 1024  # 64 MiB 原始数据


def bench_random_vs_sequential():
    data = make_data(SIZE)
    blob = compress(data, block_size=64 * 1024)
    print(f"原始 {SIZE/2**20:.0f} MiB -> 压缩 {len(blob)/2**20:.1f} MiB, "
          f"块大小 64 KiB\n")

    rng = random.Random(1)
    positions = [rng.randrange(0, SIZE - 4096) for _ in range(1000)]

    # 1) 1000 次随机小读（每次 4 KiB）
    with Reader(blob) as r:
        t0 = time.perf_counter()
        for off in positions:
            r.read(off, 4096)
        rand_dt = time.perf_counter() - t0
        s = r.stats
        print(f"[随机读] 1000 次 x 4 KiB: {rand_dt:.3f}s "
              f"({rand_dt/1000*1e6:.0f} us/次)")
        print(f"         请求 {s.bytes_requested/2**20:.1f} MiB, "
              f"实际解压 {s.bytes_decompressed/2**20:.1f} MiB "
              f"({s.blocks_decompressed} 块)")
        # 关键对照：naive 方案每次读都要整文件解压 => 1000 x 64 MiB
        assert s.bytes_decompressed < SIZE * 2, \
            "随机读解压量应远小于 读次数 x 整文件大小"

    # 2) 整文件顺序读
    with Reader(blob) as r:
        t0 = time.perf_counter()
        r.read_all()
        seq_dt = time.perf_counter() - t0
        s = r.stats
        print(f"[顺序读] 整文件 64 MiB: {seq_dt:.3f}s")
        print(f"         解压 {s.bytes_decompressed/2**20:.0f} MiB "
              f"({s.blocks_decompressed} 块)")

    # 3) 对照：naive 方案每次随机读都整文件解压
    print(f"\n对照：整文件解压一次 {seq_dt:.3f}s，naive 方案 1000 次随机读"
          f"约需 {seq_dt*1000:.0f}s，分块方案实测仅 {rand_dt:.3f}s。")
    print()


def bench_block_size_vs_latency():
    data = make_data(SIZE)
    rng = random.Random(2)
    positions = [rng.randrange(0, SIZE - 256) for _ in range(300)]
    print("块大小 vs 随机读延迟（每次读 256 B，300 次取平均）")
    print(f"{'block_size':>12} {'压缩后':>9} {'avg_lat':>10} "
          f"{'解压字节/次':>12}")
    for bs in [4 * 1024, 16 * 1024, 64 * 1024, 256 * 1024,
               1024 * 1024, 4 * 1024 * 1024]:
        blob = compress(data, block_size=bs)
        with Reader(blob) as r:
            t0 = time.perf_counter()
            for off in positions:
                r.read(off, 256)
            dt = (time.perf_counter() - t0) / len(positions)
            print(f"{bs:>12} {len(blob)/2**20:>8.1f}M "
                  f"{dt*1e6:>9.1f}us "
                  f"{r.stats.bytes_decompressed/len(positions):>11.0f}B")
    print()


if __name__ == "__main__":
    bench_random_vs_sequential()
    bench_block_size_vs_latency()
