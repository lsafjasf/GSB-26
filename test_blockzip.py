"""blockzip 单元测试 + 对拍测试（stdlib unittest）。

运行: python3 test_blockzip.py -v
"""

import os
import random
import struct
import tempfile
import unittest

from blockzip import (
    BlockChecksumError,
    BlockReader,
    ReadOutOfRangeError,
    ReadStats,
    compress,
    _HEADER,
    HEADER_SIZE,
)


def make_data(n: int, seed: int = 0) -> bytes:
    """可压缩与不可压缩混合的测试数据。"""
    rng = random.Random(seed)
    parts = []
    while n > 0:
        if rng.random() < 0.5:
            chunk = bytes(rng.getrandbits(8) for _ in range(min(n, 4096)))
        else:
            word = bytes([rng.getrandbits(8)]) * rng.randint(1, 64)
            chunk = (word * (min(n, 4096) // len(word) + 1))[: min(n, 4096)]
        parts.append(chunk)
        n -= len(chunk)
    return b"".join(parts)


class TestBasic(unittest.TestCase):
    def test_empty_data(self):
        blob = compress(b"", block_size=1024)
        r = BlockReader(blob)
        self.assertEqual(len(r), 0)
        self.assertEqual(r.read(0, 0), b"")
        with self.assertRaises(ReadOutOfRangeError):
            r.read(0, 1)

    def test_single_block(self):
        data = make_data(1000, seed=1)
        r = BlockReader(compress(data, block_size=64 * 1024))
        self.assertEqual(len(r.index), 1)
        self.assertEqual(r.read(0, len(data)), data)
        self.assertEqual(r.read(500, 100), data[500:600])

    def test_oversized_single_block(self):
        """超长单块：block_size 远大于数据，全部落在一个块里。"""
        data = make_data(3 * 1024 * 1024, seed=2)
        r = BlockReader(compress(data, block_size=64 * 1024 * 1024))
        self.assertEqual(len(r.index), 1)
        self.assertEqual(r.read(0, len(data)), data)
        self.assertEqual(r.read(2 * 1024 * 1024, 12345), data[2 * 1024 * 1024 : 2 * 1024 * 1024 + 12345])

    def test_exact_block_boundary(self):
        data = make_data(4 * 4096, seed=3)
        r = BlockReader(compress(data, block_size=4096))
        self.assertEqual(len(r.index), 4)
        self.assertEqual(r.read(4096, 4096), data[4096:8192])
        self.assertEqual(r.read(0, len(data)), data)

    def test_out_of_range(self):
        data = make_data(10_000, seed=4)
        r = BlockReader(compress(data, block_size=1024))
        for off, ln in [(-1, 1), (0, -1), (10_000, 1), (9_999, 2), (20_000, 10)]:
            with self.assertRaises(ReadOutOfRangeError, msg=f"read({off},{ln})"):
                r.read(off, ln)
        # 边界合法值不报错
        self.assertEqual(r.read(9_999, 1), data[9_999:10_000])
        self.assertEqual(r.read(10_000, 0), b"")


class TestCrossBlockDifferential(unittest.TestCase):
    """对拍：随机范围读取结果必须与原始数据切片完全一致。"""

    def test_random_reads_match_original(self):
        rng = random.Random(42)
        for seed, (size, bs) in enumerate(
            [(0, 1024), (1, 1024), (100_000, 4096), (1 << 20, 65536), (777, 16)]
        ):
            data = make_data(size, seed=100 + seed)
            r = BlockReader(compress(data, block_size=bs))
            for _ in range(300):
                if size == 0:
                    off, ln = 0, 0
                else:
                    off = rng.randrange(0, size)
                    ln = rng.randrange(0, min(size - off, 8 * bs) + 1)
                got = r.read(off, ln)
                want = data[off : off + ln]
                self.assertEqual(got, want, f"size={size} bs={bs} read({off},{ln})")

    def test_cross_block_stats(self):
        """跨块读取只解压涉及的块，不整文件解压。"""
        bs = 4096
        data = make_data(10 * bs, seed=7)
        stats = ReadStats()
        r = BlockReader(compress(data, block_size=bs), stats=stats)
        # 跨 3 个块的读取
        r.read(bs - 10, bs + 20)
        off, ln, blocks, decomp = stats.per_read[-1]
        self.assertEqual(blocks, 3)
        self.assertEqual(decomp, 3 * bs)
        # 单块内的小读取只解压 1 块
        r.read(2 * bs + 100, 50)
        self.assertEqual(stats.per_read[-1][2], 1)
        self.assertEqual(stats.per_read[-1][3], bs)
        # 总解压量远小于全量
        self.assertLess(stats.bytes_decompressed, len(data))


class TestChecksum(unittest.TestCase):
    def _corrupt_block(self, blob: bytes, block_index: int, block_size: int) -> bytes:
        """篡改指定块的压缩数据（保持长度不变）。"""
        r = BlockReader(blob)
        info = r.index[block_index]
        buf = bytearray(blob)
        buf[info.comp_off] ^= 0xFF  # 翻转压缩数据首字节
        return bytes(buf)

    def test_corruption_locates_block(self):
        bs = 2048
        data = make_data(6 * bs, seed=9)
        blob = compress(data, block_size=bs)
        bad = self._corrupt_block(blob, 3, bs)
        r = BlockReader(bad)
        with self.assertRaises(BlockChecksumError) as ctx:
            r.read(3 * bs, 100)
        self.assertEqual(ctx.exception.block_index, 3)
        # 未损坏的块仍然可读
        self.assertEqual(r.read(0, 100), data[0:100])
        self.assertEqual(r.read(5 * bs, 100), data[5 * bs : 5 * bs + 100])

    def test_corruption_never_returns_bad_data(self):
        bs = 1024
        data = make_data(4 * bs, seed=11)
        blob = compress(data, block_size=bs)
        for blk in range(4):
            bad = self._corrupt_block(blob, blk, bs)
            r = BlockReader(bad)
            try:
                got = r.read(blk * bs, bs)
                # 极少数情况下篡改后 crc 仍通过（不可能但防御）：也绝不能静默返回错数据
                self.assertEqual(got, data[blk * bs : (blk + 1) * bs])
            except BlockChecksumError as e:
                self.assertEqual(e.block_index, blk)

    def test_truncated_file(self):
        from blockzip import FormatError
        data = make_data(4096, seed=13)
        blob = compress(data, block_size=1024)
        with self.assertRaises((FormatError, BlockChecksumError)):
            BlockReader(blob[: len(blob) - 10]).read(0, 4096)


class TestFileRoundTrip(unittest.TestCase):
    def test_file_roundtrip(self):
        data = make_data(200_000, seed=17)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.bzbx")
            with open(path, "wb") as f:
                f.write(compress(data, block_size=8192))
            with BlockReader(path) as r:
                self.assertEqual(r.read(0, len(data)), data)
                self.assertEqual(r.read(123_456, 7_777), data[123_456 : 123_456 + 7_777])


if __name__ == "__main__":
    unittest.main()
