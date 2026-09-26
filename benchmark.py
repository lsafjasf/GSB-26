"""不同块大小下的读取吞吐基准。

用法: python3 benchmark.py [文件大小MiB] [每个块大小重复次数]
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from chunk_reader import read_in_chunks

CHUNK_SIZES = [
    ("4 KiB", 4 << 10),
    ("16 KiB", 16 << 10),
    ("64 KiB", 64 << 10),
    ("256 KiB", 256 << 10),
    ("1 MiB", 1 << 20),
    ("4 MiB", 4 << 20),
    ("16 MiB", 16 << 20),
    ("64 MiB", 64 << 20),
]


def main():
    size_mib = int(sys.argv[1]) if len(sys.argv) > 1 else 256
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    size = size_mib << 20

    fd, path = tempfile.mkstemp()
    try:
        block = os.urandom(1 << 20)
        written = 0
        while written < size:
            n = min(len(block), size - written)
            os.write(fd, block[:n])
            written += n
        os.close(fd)

        # 预热页缓存，排除首次磁盘 IO 影响
        read_in_chunks(path, 4 << 20)

        print("文件大小: {} MiB, 每个块大小取 {} 次中最快".format(size_mib, repeats))
        print("{:>10} {:>14} {:>12}".format("块大小", "吞吐 (MiB/s)", "相对 4KiB"))
        base = None
        for label, chunk in CHUNK_SIZES:
            best = 0.0
            for _ in range(repeats):
                t0 = time.perf_counter()
                read_in_chunks(path, chunk)
                dt = time.perf_counter() - t0
                best = max(best, size / dt)
            mib_s = best / (1 << 20)
            if base is None:
                base = mib_s
            print("{:>10} {:>14.1f} {:>11.2f}x".format(label, mib_s, mib_s / base))
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
