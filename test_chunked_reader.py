"""复现四类现网缺陷的测试 + 修复后实现的回归/逐字节对拍测试。

运行：python3 -m unittest test_chunked_reader -v
"""

import os
import random
import tempfile
import unittest

import chunked_reader_buggy as buggy
import chunked_reader as fixed


# ---------------------------------------------------------------- 测试工具

class MockFile:
    """内存文件对象，可模拟：
    - 短读：单次 read/readinto 最多返回 max_per_read 字节（未 EOF 也返回不足量）
    - 中途故障：累计读出 fail_after 字节后抛 OSError（模拟读取中被截断/设备错误）
    """

    def __init__(self, data, max_per_read=None, fail_after=None):
        self._data = data
        self._pos = 0
        self._served = 0
        self.max_per_read = max_per_read
        self.fail_after = fail_after

    # -- 固定实现使用的接口 --
    def read(self, n=-1):
        self._maybe_fail()
        if self._pos >= len(self._data):
            return b""
        if n is None or n < 0:
            n = len(self._data) - self._pos
        if self.max_per_read is not None:
            n = min(n, self.max_per_read)
        end = min(self._pos + n, len(self._data))
        chunk = self._data[self._pos:end]
        self._pos = end
        self._served += len(chunk)
        return chunk

    # -- 缺陷实现使用的接口 --
    def readinto(self, buf):
        self._maybe_fail()
        if self._pos >= len(self._data):
            return 0
        n = len(buf)
        if self.max_per_read is not None:
            n = min(n, self.max_per_read)
        end = min(self._pos + n, len(self._data))
        chunk = self._data[self._pos:end]
        buf[: len(chunk)] = chunk
        self._pos = end
        self._served += len(chunk)
        return len(chunk)

    def _maybe_fail(self):
        if self.fail_after is not None and self._served >= self.fail_after:
            raise OSError("simulated I/O error: file truncated mid-read")

    def tell(self):
        return self._pos

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_SET:
            self._pos = offset
        elif whence == os.SEEK_CUR:
            self._pos += offset
        elif whence == os.SEEK_END:
            self._pos = len(self._data) + offset
        return self._pos


class TruncatingFile:
    """包装真实文件对象：读出 trigger 字节后把底层文件截断为 new_size。"""

    def __init__(self, fileobj, path, trigger, new_size):
        self._f = fileobj
        self._path = path
        self._trigger = trigger
        self._new_size = new_size
        self._read = 0
        self._done = False

    def read(self, n=-1):
        data = self._f.read(n)
        self._read += len(data)
        if not self._done and self._read >= self._trigger:
            os.truncate(self._path, self._new_size)
            self._done = True
        return data

    def fileno(self):
        return self._f.fileno()


def make_content(size, seed=1234):
    """确定性伪随机内容（含 0x00/0xFF 等边界字节）。"""
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(size))


def write_temp(content):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


# ------------------------------------------------- 第一部分：复现四类现网缺陷

class TestBuggyReproduction(unittest.TestCase):
    """这些测试断言缺陷实现确实表现出四类问题（修复前应当全部成立）。"""

    def test_defect4_tail_chunk_silently_dropped(self):
        # 文件大小不整除块大小：3*4096 + 7，末尾 7 字节被静默丢弃
        content = make_content(3 * 4096 + 7)
        path = write_temp(content)
        try:
            out = buggy.read_file_in_chunks(path, 4096)
        finally:
            os.unlink(path)
        self.assertNotEqual(out, content)
        self.assertEqual(len(out), 3 * 4096)          # 尾巴 7 字节丢失
        self.assertEqual(out, content[: 3 * 4096])

    def test_defect1_stale_bytes_reprocessed(self):
        # 短读时未按实际字节数切片，缓冲区残留字节被重复拼接
        content = b"0123456789ABCDEF"                 # 16 字节，2 个整块
        mock = MockFile(content, max_per_read=5)      # 每次 readinto 最多 5 字节
        out = buggy.read_file_in_chunks(mock, 8)
        self.assertNotEqual(out, content)
        self.assertIn(b"\x00\x00\x00", out)           # 残留/陈旧字节混入输出

    def test_defect3_offset_misaligned_across_chunks(self):
        # 短读后偏移仍按 chunk_size 累加并强制 seek，中间字节被跳过
        content = b"0123456789ABCDEF"
        mock = MockFile(content, max_per_read=5)
        out = buggy.read_file_in_chunks(mock, 8)
        self.assertNotEqual(out, content)
        self.assertNotIn(b"567", out)                 # 偏移错位导致 "567" 被跳过

    def test_defect2_uncaught_exception_on_io_error(self):
        # 读取中途底层抛 OSError：缺陷实现不做任何捕获，异常直接外泄
        content = make_content(4 * 4096)
        mock = MockFile(content, fail_after=4096)     # 读完第一块后故障
        with self.assertRaises(OSError):
            buggy.read_file_in_chunks(mock, 4096)


# ------------------------------------------------- 第二部分：修复后的回归测试

class TestFixedByteExact(unittest.TestCase):
    """逐字节对拍：任意大小、任意块大小，拼接结果必须等于文件内容。"""

    SIZES = [0, 1, 2, 7, 4095, 4096, 4097, 3 * 4096, 3 * 4096 + 7, 100003]
    CHUNKS = [1, 2, 3, 7, 4096, 65536]

    def test_byte_exact_matrix_via_path(self):
        for size in self.SIZES:
            content = make_content(size)
            path = write_temp(content)
            try:
                for chunk in self.CHUNKS:
                    with self.subTest(size=size, chunk=chunk):
                        r = fixed.read_chunked(path, chunk)
                        self.assertTrue(r.ok, r)
                        self.assertEqual(r.bytes_read, size)
                        self.assertEqual(r.data, content)   # 逐字节相等
            finally:
                os.unlink(path)

    def test_byte_exact_matrix_via_fileobj(self):
        for size in self.SIZES:
            content = make_content(size)
            for chunk in self.CHUNKS:
                with self.subTest(size=size, chunk=chunk):
                    r = fixed.read_chunked(MockFile(content), chunk)
                    self.assertTrue(r.ok, r)
                    self.assertEqual(r.data, content)

    def test_iter_chunks_concat_equals_content(self):
        content = make_content(3 * 4096 + 7)
        chunks = list(fixed.iter_chunks(MockFile(content), 4096))
        self.assertEqual(len(chunks), 4)
        self.assertEqual(len(chunks[-1]), 7)              # 不完整末块被正确产出
        self.assertEqual(b"".join(chunks), content)

    def test_empty_file(self):
        path = write_temp(b"")
        try:
            r = fixed.read_chunked(path, 4096)
        finally:
            os.unlink(path)
        self.assertTrue(r.ok)
        self.assertEqual(r.data, b"")
        self.assertEqual(r.bytes_read, 0)

    def test_single_byte(self):
        path = write_temp(b"\xAB")
        try:
            r = fixed.read_chunked(path, 4096)
        finally:
            os.unlink(path)
        self.assertTrue(r.ok)
        self.assertEqual(r.data, b"\xAB")

    def test_chunk_size_one(self):
        content = make_content(1000)
        r = fixed.read_chunked(MockFile(content), 1)
        self.assertTrue(r.ok)
        self.assertEqual(r.data, content)

    def test_oversized_chunk(self):
        # 块远大于文件：16 MiB 块读 1 MiB 文件
        content = make_content(1 << 20)
        r = fixed.read_chunked(MockFile(content), 1 << 24)
        self.assertTrue(r.ok)
        self.assertEqual(r.data, content)

    def test_file_smaller_than_one_chunk(self):
        content = make_content(100)
        r = fixed.read_chunked(MockFile(content), 4096)
        self.assertTrue(r.ok)
        self.assertEqual(r.data, content)

    def test_short_read_still_byte_exact(self):
        # 模拟短读：单次 read 最多返回 5 字节，结果仍须逐字节正确
        content = make_content(3 * 4096 + 7)
        for chunk in (1, 8, 4096, 65536):
            with self.subTest(chunk=chunk):
                r = fixed.read_chunked(MockFile(content, max_per_read=5), chunk)
                self.assertTrue(r.ok, r)
                self.assertEqual(r.data, content)

    def test_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            fixed.read_chunked(MockFile(b"x"), 0)
        with self.assertRaises(ValueError):
            list(fixed.iter_chunks(MockFile(b"x"), -1))


class TestFixedErrorHandling(unittest.TestCase):
    """中途截断 / 读取出错：返回可区分错误并指出已读字节数，不静默丢弃。"""

    def test_io_error_returns_distinguishable_error(self):
        content = make_content(4 * 4096)
        mock = MockFile(content, fail_after=4096)     # 读满 4096 字节后抛 OSError
        r = fixed.read_chunked(mock, 4096)
        self.assertFalse(r.ok)
        self.assertIsInstance(r.error, fixed.ChunkedIOError)       # 可区分类型
        self.assertNotIsInstance(r.error, fixed.TruncatedReadError)
        self.assertEqual(r.bytes_read, 4096)          # 失败前已读 1 个整块
        self.assertEqual(r.error.bytes_read, 4096)
        self.assertEqual(r.data, content[:4096])      # 已读部分不丢弃
        self.assertIsInstance(r.error.original, OSError)

    def test_real_truncation_detected(self):
        # 真实截断：读到一半时把文件截小，读取只能提前 EOF
        content = make_content(8 * 4096)
        path = write_temp(content)
        try:
            f = open(path, "rb")
            wrapper = TruncatingFile(f, path, trigger=4096, new_size=6000)
            r = fixed.read_chunked(wrapper, 4096)
            f.close()
        finally:
            os.unlink(path)
        self.assertFalse(r.ok)
        self.assertIsInstance(r.error, fixed.TruncatedReadError)   # 可区分类型
        self.assertEqual(r.bytes_read, 6000)          # 指出已读字节数
        self.assertEqual(r.data, content[:6000])

    def test_partial_data_never_silently_dropped(self):
        content = make_content(10000)
        mock = MockFile(content, fail_after=7777)
        r = fixed.read_chunked(mock, 4096)
        self.assertFalse(r.ok)
        self.assertEqual(r.bytes_read + (len(content) - r.bytes_read), len(content))
        self.assertEqual(r.data, content[: r.bytes_read])


if __name__ == "__main__":
    unittest.main()
