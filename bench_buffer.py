"""缓冲峰值测量：验证内部缓冲有上界，且与喂入切分方式无关。

运行: python3 bench_buffer.py
"""

import tracemalloc

from framing import (BufferLimitExceededError, DelimiterFramer,
                     FrameTooLargeError, LengthPrefixFramer)

MAX_FRAME = 1 << 16          # 64 KiB 帧上限
FRAMES = 200
CHUNK_SIZES = [1, 7, 4096, 1 << 20]


def run_length_prefix():
    f = LengthPrefixFramer(max_frame_size=MAX_FRAME)
    msg = bytes(MAX_FRAME)                      # 满尺寸帧，最坏情况
    stream = f.encode(msg) * FRAMES             # 200 个满帧 ≈ 12.8 MiB
    print(f"[length-prefix] stream={len(stream)/2**20:.1f} MiB, "
          f"bound=header+max_frame={f.max_buffer} bytes")
    for chunk in CHUNK_SIZES:
        framer = LengthPrefixFramer(max_frame_size=MAX_FRAME)
        tracemalloc.start()
        n = 0
        for pos in range(0, len(stream), chunk):
            n += len(framer.feed(stream[pos:pos + chunk]))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert n == FRAMES
        print(f"  chunk={chunk:>8}: frames={n}, "
              f"buffer_peak={framer.buffer_peak:>7} B, "
              f"tracemalloc_peak={peak/2**20:.2f} MiB")
        assert framer.buffer_peak <= framer.max_buffer + chunk


def run_delimiter():
    f = DelimiterFramer(delimiter=b"\n", max_frame_size=MAX_FRAME)
    msg = bytes(b"x" * MAX_FRAME) + b"\n"
    stream = msg * FRAMES
    print(f"[delimiter]     stream={len(stream)/2**20:.1f} MiB, "
          f"bound=max_buffer={f.max_buffer} bytes")
    for chunk in CHUNK_SIZES:
        framer = DelimiterFramer(delimiter=b"\n", max_frame_size=MAX_FRAME)
        tracemalloc.start()
        n = 0
        for pos in range(0, len(stream), chunk):
            n += len(framer.feed(stream[pos:pos + chunk]))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert n == FRAMES
        print(f"  chunk={chunk:>8}: frames={n}, "
              f"buffer_peak={framer.buffer_peak:>7} B, "
              f"tracemalloc_peak={peak/2**20:.2f} MiB")
        assert framer.buffer_peak <= framer.max_buffer + chunk


def run_attacks():
    print("[attack] malicious length / missing delimiter")
    framer = LengthPrefixFramer(max_frame_size=1024)
    try:
        framer.feed((2**32 - 1).to_bytes(4, "big") + b"x" * 10_000)
    except FrameTooLargeError as e:
        print(f"  length-prefix rejected: {e}; buffered={framer.buffered}")
    framer = DelimiterFramer(delimiter=b"\n", max_frame_size=1024)
    try:
        for _ in range(1000):
            framer.feed(b"z" * 1000)            # 1 MB 无分隔符数据
    except BufferLimitExceededError as e:
        print(f"  delimiter rejected:     {e}; buffered={framer.buffered}")


if __name__ == "__main__":
    run_length_prefix()
    run_delimiter()
    run_attacks()
    print("OK: buffer peaks bounded by configured limits for all chunkings")
