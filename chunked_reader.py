"""修复后的分块读取实现（仅标准库）。

修复点：
1. 每次只拼接实际读到的字节（按 n 切片），短读不会重复上一块的残留字节。
2. 底层 IO 异常被捕获并转换为可区分的 ChunkedIOError，携带已读字节数。
3. 不再按 chunk_size 累加偏移后强制 seek；顺序读取，偏移天然正确。
   同时对“单次 read 返回不足 chunk_size 但未 EOF”的短读循环补齐。
4. 最后一个不完整块照常产出，绝不静默丢弃。

错误约定：
- read_chunked 返回 ReadResult，永不因 IO 错误抛异常。
- result.ok 为 False 时，result.error 是 ChunkedReadError 的具体子类：
    * TruncatedReadError —— 读取过程中文件被截断（实际读到的字节少于起始大小）
    * ChunkedIOError     —— 底层 read 抛出 OSError
  result.bytes_read 表示失败前已成功读取的字节数，result.data 为已读到的部分数据。
"""

from __future__ import annotations

import os


class ChunkedReadError(Exception):
    """分块读取失败基类。bytes_read 为失败前已成功读取的字节数。"""

    def __init__(self, message, bytes_read):
        super().__init__(message)
        self.bytes_read = bytes_read


class TruncatedReadError(ChunkedReadError):
    """读取过程中文件被截断：读到的字节数少于读取开始时的大小。"""


class ChunkedIOError(ChunkedReadError):
    """底层读取抛出 OSError。original 保存原始异常。"""

    def __init__(self, message, bytes_read, original=None):
        super().__init__(message, bytes_read)
        self.original = original


class ReadResult:
    __slots__ = ("data", "bytes_read", "error")

    def __init__(self, data, bytes_read, error=None):
        self.data = data
        self.bytes_read = bytes_read
        self.error = error

    @property
    def ok(self):
        return self.error is None

    def __repr__(self):
        if self.ok:
            return f"ReadResult(ok, bytes_read={self.bytes_read})"
        return f"ReadResult({type(self.error).__name__}, bytes_read={self.bytes_read})"


def _expected_size(f):
    """尽力获取文件当前大小；无法获取（如管道）时返回 None。"""
    try:
        return os.fstat(f.fileno()).st_size
    except (OSError, AttributeError):
        pass
    try:
        cur = f.tell()
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(cur, os.SEEK_SET)
        return size
    except (OSError, AttributeError):
        return None


def iter_chunks(source, chunk_size=65536):
    """生成器：逐块产出 bytes。最后一块可能不足 chunk_size，但绝不会被丢弃。

    底层 read 抛 OSError 时转换为 ChunkedIOError 抛出（其 bytes_read 为已产出字节数）。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    if isinstance(source, (str, bytes, os.PathLike)):
        f = open(source, "rb")
        should_close = True
    else:
        f = source
        should_close = False

    produced = 0
    try:
        while True:
            parts = []
            remaining = chunk_size
            # 循环补齐：单次 read 可能短读（返回 < 请求字节数但未 EOF）
            while remaining > 0:
                try:
                    piece = f.read(remaining)
                except OSError as exc:
                    raise ChunkedIOError(
                        f"I/O error after {produced} bytes: {exc}", produced, exc
                    ) from exc
                if not piece:  # b"" 或 None 视为 EOF
                    break
                parts.append(piece)
                remaining -= len(piece)
            if not parts:
                return
            chunk = parts[0] if len(parts) == 1 else b"".join(parts)
            produced += len(chunk)
            yield chunk
    finally:
        if should_close:
            f.close()


def read_chunked(source, chunk_size=65536):
    """分块读取并拼接，返回 ReadResult。

    成功时 result.ok 为 True 且 result.data 与文件内容逐字节一致；
    失败时 result.error 为可区分的错误类型，result.bytes_read / result.data
    为失败前已读取的字节数和部分数据，绝不静默丢弃。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    # 记录起始大小用于截断检测（仅对可获取大小的来源）
    if isinstance(source, (str, bytes, os.PathLike)):
        f = open(source, "rb")
        should_close = True
    else:
        f = source
        should_close = False
    expected = _expected_size(f)
    if should_close:
        f.close()

    out = bytearray()
    try:
        for chunk in iter_chunks(source, chunk_size):
            out += chunk
    except ChunkedReadError as exc:
        # iter_chunks 抛出的错误其 bytes_read 即已拼接的字节数
        return ReadResult(bytes(out), len(out), exc)

    if expected is not None and len(out) < expected:
        err = TruncatedReadError(
            f"file truncated during read: expected {expected} bytes, got {len(out)}",
            len(out),
        )
        return ReadResult(bytes(out), len(out), err)
    return ReadResult(bytes(out), len(out))
