"""不同块大小下的读取吞吐基准。

运行：python3 benchmark.py [文件大小MiB]
"""

import os
import sys
import tempfile
import time

from chunked_reader import read_chunked

CHUNK_SIZES = [
    ("4 KiB", 4 << 10),
    ("16 KiB", 16 << 10),
    ("64 KiB", 64 << 10),
    ("256 KiB", 256 << 10),
    ("1 MiB", 1 << 20),
    ("4 MiB", 4 << 20),
    ("16 MiB", 16 << 20),
]
REPEATS = 5


def main():
    size_mib = int(sys.argv[1]) if len(sys.argv) > 1 else 256
    size = size_mib << 20

    fd, path = tempfile.mkstemp()
    try:
        block = os.urandom(1 << 20)
        with os.fdopen(fd, "wb") as f:
            for _ in range(size_mib):
                f.write(block)

        expected = None
        print(f"file size: {size_mib} MiB, best of {REPEATS} runs (page-cache warm)")
        print(f"{'chunk':>8} | {'throughput':>12} | {'time':>9}")
        print("-" * 36)
        for label, chunk in CHUNK_SIZES:
            best = None
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                r = read_chunked(path, chunk)
                dt = time.perf_counter() - t0
                assert r.ok and r.bytes_read == size, r
                if expected is None:
                    expected = r.data
                else:
                    assert r.data == expected  # 每种块大小都逐字节一致
                if best is None or dt < best:
                    best = dt
            print(f"{label:>8} | {size / best / (1 << 20):>9.0f} MiB/s | {best*1000:>7.1f} ms")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
