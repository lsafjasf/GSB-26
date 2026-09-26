"""分块读取的缺陷复现测试 + 修复回归测试 + 逐字节对拍测试。

运行: python3 tests/test_chunk_reader.py -v
"""

import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chunk_reader_buggy
from chunk_reader import ChunkReadError, iter_chunks, read_in_chunks


def make_content(size, seed=1234):
    """生成确定性的伪随机文件内容。"""
    rng = random.Random(seed + size)
    return rng.randbytes(size) if hasattr(rng, "randbytes") else bytes(
        rng.getrandbits(8) for _ in range(size)
    )


class TempFileMixin:
    def write_temp(self, content):
        fd, path = tempfile.mkstemp()
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        self.addCleanup(os.unlink, path)
        return path


# ---------------------------------------------------------------------------
# 测试用的文件对象模拟器
# ---------------------------------------------------------------------------

class ShortReadFile:
    """模拟“短读”：每次 read 最多返回 max_piece 字节（如管道/网络文件）。"""

    def __init__(self, data, max_piece):
        self._data = data
        self._pos = 0
        self._max_piece = max_piece

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._data) - self._pos
        n = min(n, self._max_piece, len(self._data) - self._pos)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_END:
            self._pos = len(self._data) + offset
        else:
            self._pos = offset
        return self._pos

    def tell(self):
        return self._pos


class TruncatingReader:
    """包装真实文件对象：第 trigger_read 次 read 前把文件截断到 truncate_to 字节。"""

    def __init__(self, fobj, trigger_read, truncate_to):
        self._fobj = fobj
        self._trigger = trigger_read
        self._truncate_to = truncate_to
        self._reads = 0

    def read(self, n=-1):
        self._reads += 1
        if self._reads == self._trigger:
            os.ftruncate(self._fobj.fileno(), self._truncate_to)
        return self._fobj.read(n)

    def fileno(self):
        return self._fobj.fileno()

    def seek(self, offset, whence=os.SEEK_SET):
        return self._fobj.seek(offset, whence)

    def tell(self):
        return self._fobj.tell()


class ExplodingFile:
    """读到 limit 字节后抛 OSError，模拟读取中途设备/IO 错误。"""

    def __init__(self, data, limit):
        self._data = data
        self._pos = 0
        self._limit = limit

    def read(self, n=-1):
        if self._pos >= self._limit:
            raise OSError("I/O error (simulated)")
        if n is None or n < 0:
            n = len(self._data) - self._pos
        n = min(n, self._limit - self._pos, len(self._data) - self._pos)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_END:
            self._pos = len(self._data) + offset
        else:
            self._pos = offset
        return self._pos

    def tell(self):
        return self._pos


# ---------------------------------------------------------------------------
# 一、四类现网缺陷的稳定复现（针对有缺陷实现）
# ---------------------------------------------------------------------------

class TestBugReproduction(TempFileMixin, unittest.TestCase):
    """每个用例稳定复现一类现网缺陷；修复版的对应用例见 TestFixedReader。"""

    def test_bug1_eof_reprocesses_previous_chunk_bytes(self):
        """BUG 1: 读到（被截断的）文件末尾短读时，上一块的多余字节被重复处理。"""
        content = make_content(100)
        path = self.write_temp(content)
        fobj = open(path, "r+b", buffering=0)   # 无缓冲，保证截断立即生效
        self.addCleanup(fobj.close)
        # 第 2 次 read 前把文件截断到 25 字节：offset=20 处发生短读（仅 5 字节）
        truncating = TruncatingReader(fobj, trigger_read=2, truncate_to=25)
        result = chunk_reader_buggy.read_file_in_chunks(truncating, 10)
        # 缺陷实现用上一块（content[10:20]）的尾部 5 字节补齐短读块
        self.assertNotEqual(result, content)
        self.assertEqual(result[20:30], content[20:25] + content[15:20])

    def test_bug2_truncation_raises_uncaught_raw_exception(self):
        """BUG 2: 读取中途被截断/出错时，原始 OSError 未经包装直接外抛。"""
        content = make_content(100)
        fake = ExplodingFile(content, limit=30)
        with self.assertRaises(OSError) as ctx:
            chunk_reader_buggy.read_file_in_chunks(fake, 8)
        # 异常不可区分：不是专门的错误类型，也不携带已读字节数
        self.assertNotIsInstance(ctx.exception, ChunkReadError)
        self.assertFalse(hasattr(ctx.exception, "bytes_read"))

    def test_bug3_offset_misaligned_after_short_read(self):
        """BUG 3: 跨块边界短读后，后续块偏移整体错位。"""
        content = make_content(30)
        # 第 2 块开始每次 read 只给 3 字节，模拟流式短读
        fake = ShortReadFile(content, max_piece=3)
        result = chunk_reader_buggy.read_file_in_chunks(fake, 6)
        self.assertNotEqual(result, content)
        # 第 2 块起即与真实内容错位
        self.assertNotEqual(result[6:12], content[6:12])

    def test_bug4_last_incomplete_chunk_silently_dropped(self):
        """BUG 4: 最后一个不完整块被静默丢弃。"""
        content = make_content(10)          # 10 = 4*2 + 2，余 2 字节
        path = self.write_temp(content)
        result = chunk_reader_buggy.read_file_in_chunks(path, 4)
        self.assertEqual(result, content[:8])   # 末块 2 字节丢失
        self.assertNotEqual(result, content)

    def test_bug4_file_smaller_than_one_chunk_dropped(self):
        """BUG 4 特例: 文件小于一块时整个文件被丢弃。"""
        content = make_content(3)
        path = self.write_temp(content)
        result = chunk_reader_buggy.read_file_in_chunks(path, 8)
        self.assertEqual(result, b"")


# ---------------------------------------------------------------------------
# 二、修复版回归测试（针对上述四类缺陷）
# ---------------------------------------------------------------------------

class TestFixedReader(TempFileMixin, unittest.TestCase):

    def test_short_read_at_eof_no_duplication(self):
        """对应 BUG 1: 末尾短读不重复任何字节，结果逐字节等于内容。"""
        content = make_content(12)
        fake = ShortReadFile(content, max_piece=5)
        self.assertEqual(read_in_chunks(fake, 5), content)

    def test_simulated_short_read_every_read(self):
        """对应 BUG 3: 每次 read 都短读，偏移依然正确、结果逐字节相等。"""
        content = make_content(30)
        for max_piece in (1, 2, 3, 5):
            fake = ShortReadFile(content, max_piece=max_piece)
            self.assertEqual(read_in_chunks(fake, 6), content)

    def test_offsets_correct_across_chunk_boundaries(self):
        """对应 BUG 3: on_chunk 回调的偏移在跨块边界处不错位。"""
        content = make_content(23)
        seen = []
        fake = ShortReadFile(content, max_piece=3)   # 强制短读
        result = read_in_chunks(fake, 8, on_chunk=lambda c, off: seen.append((off, c)))
        self.assertEqual(result, content)
        offsets = [off for off, _ in seen]
        self.assertEqual(offsets, [0, 8, 16])
        self.assertEqual(b"".join(c for _, c in seen), content)

    def test_mid_read_truncation_real_file(self):
        """对应 BUG 2: 真实文件读取中途被截断 -> 可区分错误 + 已读字节数。"""
        content = make_content(100)
        path = self.write_temp(content)
        fobj = open(path, "r+b", buffering=0)   # 无缓冲，保证截断立即生效
        self.addCleanup(fobj.close)
        truncating = TruncatingReader(fobj, trigger_read=2, truncate_to=10)
        with self.assertRaises(ChunkReadError) as ctx:
            read_in_chunks(truncating, 10)
        err = ctx.exception
        self.assertEqual(err.bytes_read, 10)        # 第 1 块读到后文件被截断
        self.assertEqual(err.partial_data, content[:10])
        self.assertIn("截断", str(err))

    def test_mid_read_io_error_distinguishable(self):
        """对应 BUG 2: 读取中途 IO 错误 -> ChunkReadError，携带已读字节数。"""
        content = make_content(100)
        fake = ExplodingFile(content, limit=30)
        with self.assertRaises(ChunkReadError) as ctx:
            read_in_chunks(fake, 8)
        err = ctx.exception
        self.assertEqual(err.bytes_read, 30)
        self.assertEqual(err.partial_data, content[:30])
        self.assertIsInstance(err, OSError)         # 仍可按 OSError 兜底捕获

    def test_incomplete_last_chunk_not_dropped(self):
        """对应 BUG 4: 不完整末块被正常处理。"""
        content = make_content(10)
        path = self.write_temp(content)
        self.assertEqual(read_in_chunks(path, 4), content)
        chunks = [c for _, c in iter_chunks(path, 4)]
        self.assertEqual([len(c) for c in chunks], [4, 4, 2])

    def test_iter_chunks_matches_content(self):
        content = make_content(1000)
        path = self.write_temp(content)
        parts = list(iter_chunks(path, 7))
        self.assertEqual([off for off, _ in parts], list(range(0, 1000, 7)))
        self.assertEqual(b"".join(c for _, c in parts), content)

    def test_iter_chunks_truncation_error(self):
        content = make_content(50)
        fake = ExplodingFile(content, limit=20)
        it = iter_chunks(fake, 8)
        next(it)                                    # 8 字节
        next(it)                                    # 16 字节
        with self.assertRaises(ChunkReadError) as ctx:
            next(it)                                # 读到 20 字节后出错
        self.assertEqual(ctx.exception.bytes_read, 20)

    def test_invalid_chunk_size_rejected(self):
        for bad in (0, -1, -100, 1.5, "8", None, True):
            with self.assertRaises(ValueError):
                read_in_chunks(self.write_temp(b"abc"), bad)
            with self.assertRaises(ValueError):
                list(iter_chunks(self.write_temp(b"abc"), bad))


# ---------------------------------------------------------------------------
# 三、逐字节对拍：任意大小、任意块大小，拼接结果必须等于文件内容
# ---------------------------------------------------------------------------

class TestByteExactRoundTrip(TempFileMixin, unittest.TestCase):

    SIZES = [0, 1, 2, 3, 5, 7, 8, 9, 15, 16, 17, 63, 64, 65,
             127, 128, 255, 256, 257, 1000, 4095, 4096, 4097,
             65535, 65536, 65537, 1_000_003]
    CHUNKS = [1, 2, 3, 5, 8, 16, 64, 1000, 4096, 65536,
              1 << 20, 1 << 31]

    def test_roundtrip_all_combinations(self):
        for size in self.SIZES:
            content = make_content(size)
            path = self.write_temp(content)
            for chunk_size in self.CHUNKS:
                with self.subTest(size=size, chunk_size=chunk_size):
                    self.assertEqual(read_in_chunks(path, chunk_size), content)

    def test_roundtrip_via_short_read_fileobj(self):
        """文件对象每次 read 最多 3 字节（短读），结果仍须逐字节相等。"""
        for size in (0, 1, 2, 100, 4097):
            content = make_content(size)
            for chunk_size in (1, 2, 7, 64, 4096):
                with self.subTest(size=size, chunk_size=chunk_size):
                    fake = ShortReadFile(content, max_piece=3)
                    self.assertEqual(read_in_chunks(fake, chunk_size), content)

    def test_empty_file(self):
        path = self.write_temp(b"")
        for chunk_size in (1, 8, 1 << 31):
            self.assertEqual(read_in_chunks(path, chunk_size), b"")
            self.assertEqual(list(iter_chunks(path, chunk_size)), [])

    def test_single_byte_file(self):
        path = self.write_temp(b"\xab")
        for chunk_size in (1, 2, 4096, 1 << 31):
            self.assertEqual(read_in_chunks(path, chunk_size), b"\xab")

    def test_chunk_size_one(self):
        content = make_content(257)
        path = self.write_temp(content)
        self.assertEqual(read_in_chunks(path, 1), content)
        chunks = [c for _, c in iter_chunks(path, 1)]
        self.assertEqual(len(chunks), 257)

    def test_oversized_chunk(self):
        """块大小远大于文件（含超大块）时一次读完，不丢字节。"""
        content = make_content(1234)
        path = self.write_temp(content)
        for chunk_size in (1234, 1235, 1 << 20, 1 << 31):
            self.assertEqual(read_in_chunks(path, chunk_size), content)

    def test_exact_multiple_and_off_by_one(self):
        """整除边界：size == k * chunk 与 k * chunk ± 1。"""
        for chunk_size in (1, 4, 4096):
            for k in (1, 3):
                for delta in (-1, 0, 1):
                    size = k * chunk_size + delta
                    if size < 0:
                        continue
                    content = make_content(size)
                    path = self.write_temp(content)
                    with self.subTest(size=size, chunk_size=chunk_size):
                        self.assertEqual(read_in_chunks(path, chunk_size), content)


if __name__ == "__main__":
    unittest.main()
