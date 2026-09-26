"""blockzip 自测：跨块对拍、边界情形、校验定位、读取统计。"""

import os
import random
import struct
import tempfile
import unittest

import blockzip
from blockzip import ChecksumError, RangeError, Reader, compress


def make_data(n, seed=42):
    rng = random.Random(seed)
    # 混合可压缩与随机内容，贴近真实数据
    parts = []
    while n > 0:
        if rng.random() < 0.5:
            chunk = (b"the quick brown fox " * 64)[:min(n, 1280)]
        else:
            chunk = rng.randbytes(min(n, 1280))
        parts.append(chunk)
        n -= len(chunk)
    return b"".join(parts)


class CrossBlockTest(unittest.TestCase):
    """跨块读取与原始数据对拍。"""

    BLOCK = 1024
    SIZE = 1024 * 10 + 333  # 跨 11 块，最后一块不满

    @classmethod
    def setUpClass(cls):
        cls.data = make_data(cls.SIZE)
        cls.blob = compress(cls.data, block_size=cls.BLOCK)

    def test_roundtrip_full(self):
        with Reader(self.blob) as r:
            self.assertEqual(r.read_all(), self.data)

    def test_random_reads_match_original(self):
        rng = random.Random(7)
        with Reader(self.blob) as r:
            for _ in range(2000):
                off = rng.randrange(0, self.SIZE)
                ln = rng.randrange(0, min(4096, self.SIZE - off) + 1)
                self.assertEqual(r.read(off, ln), self.data[off:off + ln])

    def test_explicit_cross_block_boundaries(self):
        with Reader(self.blob) as r:
            for i in range(1, self.SIZE // self.BLOCK + 1):
                b = i * self.BLOCK
                for off, ln in [(b - 1, 2), (b - 5, 10), (b - 1024, 2048),
                                (b, 1), (b - 1, 1)]:
                    if off >= 0 and off + ln <= self.SIZE:
                        self.assertEqual(r.read(off, ln),
                                         self.data[off:off + ln],
                                         f"off={off} len={ln}")


class EdgeCaseTest(unittest.TestCase):
    def test_empty(self):
        blob = compress(b"", block_size=64)
        with Reader(blob) as r:
            self.assertEqual(len(r), 0)
            self.assertEqual(r.block_count, 0)
            self.assertEqual(r.read_all(), b"")
            self.assertEqual(r.read(0, 0), b"")
            with self.assertRaises(RangeError):
                r.read(0, 1)

    def test_single_block(self):
        data = make_data(500)
        blob = compress(data, block_size=4096)
        with Reader(blob) as r:
            self.assertEqual(r.block_count, 1)
            self.assertEqual(r.read(0, 500), data)
            self.assertEqual(r.read(100, 200), data[100:300])

    def test_oversized_single_block(self):
        # 单块远大于默认块大小（5 MiB 一块）
        data = make_data(5 * 1024 * 1024 + 7)
        blob = compress(data, block_size=8 * 1024 * 1024)
        with Reader(blob) as r:
            self.assertEqual(r.block_count, 1)
            rng = random.Random(3)
            for _ in range(50):
                off = rng.randrange(0, len(data))
                ln = rng.randrange(1, min(8192, len(data) - off) + 1)
                self.assertEqual(r.read(off, ln), data[off:off + ln])

    def test_out_of_range(self):
        data = make_data(1000)
        blob = compress(data, block_size=256)
        with Reader(blob) as r:
            for off, ln in [(-1, 1), (0, 1001), (999, 2), (1000, 1),
                            (0, -1)]:
                with self.assertRaises(RangeError, msg=f"{off},{ln}"):
                    r.read(off, ln)
            # 边界本身合法
            self.assertEqual(r.read(999, 1), data[999:1000])
            self.assertEqual(r.read(0, 1000), data)
            self.assertEqual(r.read(1000, 0), b"")  # EOF 处读 0 字节合法


class ChecksumTest(unittest.TestCase):
    def test_corruption_locates_block_and_rejects(self):
        data = make_data(4096)
        blob = bytearray(compress(data, block_size=512))
        with Reader(bytes(blob)) as r:
            # 找到第 3 块的压缩数据位置并翻转一个字节
            comp_offset, comp_size, _, _ = r._index[2]
        blob[comp_offset + comp_size // 2] ^= 0xFF
        with Reader(bytes(blob)) as r:
            # 不碰第 3 块的读取仍然正常
            self.assertEqual(r.read(0, 512), data[0:512])
            # 碰到第 3 块必须报错且指出块号
            with self.assertRaises(ChecksumError) as ctx:
                r.read(2 * 512, 512)
            self.assertEqual(ctx.exception.block_index, 2)
            with self.assertRaises(ChecksumError):
                r.read(2 * 512 + 511, 2)  # 跨块读同样被拒绝


class StatsTest(unittest.TestCase):
    def test_random_read_does_not_decompress_whole_file(self):
        data = make_data(1024 * 1024)
        blob = compress(data, block_size=4096)
        with Reader(blob) as r:
            got = r.read(100_000, 16)
            self.assertEqual(got, data[100_000:100_016])
            self.assertEqual(r.stats.blocks_decompressed, 1)
            self.assertEqual(r.stats.bytes_decompressed, 4096)
            self.assertLess(r.stats.bytes_decompressed, len(data))

    def test_cross_block_stats(self):
        data = make_data(1024 * 1024)
        blob = compress(data, block_size=4096)
        with Reader(blob) as r:
            r.read(4096 - 8, 16)  # 跨 2 块
            self.assertEqual(r.stats.blocks_decompressed, 2)
            self.assertEqual(r.stats.bytes_decompressed, 8192)


class FileRoundTripTest(unittest.TestCase):
    def test_compress_file_and_read_from_path(self):
        data = make_data(100_000)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.bin")
            dst = os.path.join(d, "dst.bzlk")
            with open(src, "wb") as f:
                f.write(data)
            blockzip.compress_file(src, dst, block_size=8192)
            with Reader(dst) as r:
                self.assertEqual(r.read_all(), data)
                self.assertEqual(r.read(50_000, 1234),
                                 data[50_000:51_234])


if __name__ == "__main__":
    unittest.main(verbosity=2)
