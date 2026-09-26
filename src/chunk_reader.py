"""分块读取大文件 —— 修复版本（仅依赖标准库）。

修复点（对应现网四类缺陷）：
  1. 短读处理：以每次 read 实际返回的字节数为准，绝不重用上一块的数据；
     循环读取直到凑满一块或到达 EOF，拼接结果逐字节等于文件内容。
  2. 错误处理：读取过程中的 OSError 与“读取中途文件被截断”统一包装为
     ChunkReadError，携带已读字节数 bytes_read 与已读数据 partial_data，
     可区分、可恢复，绝不静默丢弃。
  3. 偏移计算：偏移完全由“实际已消费字节数”推导（顺序读，不依赖固定
     步长 seek），短读不会造成跨块边界错位。
  4. 不完整末块：读至 EOF 为止，最后一个不完整块（包括小于一整块的
     整个文件、空文件）都会被正常返回/回调，绝不静默丢弃。
"""

import os

__all__ = ["ChunkReadError", "read_in_chunks", "iter_chunks"]


class ChunkReadError(OSError):
    """分块读取失败（中途截断 / IO 错误）的可区分错误类型。

    属性:
        bytes_read:   出错前已成功读取的字节数。
        partial_data: 出错前已读取到的完整数据前缀。
    """

    def __init__(self, message, bytes_read, partial_data=b""):
        super().__init__(message)
        self.bytes_read = bytes_read
        self.partial_data = partial_data


def _open_source(source):
    """返回 (fileobj, should_close)。支持路径或已打开的二进制文件对象。"""
    if isinstance(source, (str, bytes, os.PathLike)):
        return open(source, "rb"), True
    return source, False


def _expected_size(fobj):
    """尽力获取文件初始大小用于截断检测；取不到则返回 None。"""
    try:
        return os.fstat(fobj.fileno()).st_size
    except (AttributeError, OSError):
        return None


def read_in_chunks(source, chunk_size, on_chunk=None):
    """按固定大小分块读取 source，返回完整内容（bytes）。

    source:     文件路径，或已打开的二进制文件对象（调用方负责关闭）。
    chunk_size: 块大小，必须为 >= 1 的整数。
    on_chunk:   可选回调 on_chunk(chunk: bytes, offset: int)，按顺序对
                每一块（含不完整末块）回调，offset 为该块起始字节偏移。

    返回值为各块按序拼接的结果，逐字节等于文件内容。

    读取中途发生 IO 错误或文件被截断时抛出 ChunkReadError，
    其 bytes_read / partial_data 指明出错前已读取的字节数与数据。
    """
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
        raise ValueError("chunk_size 必须是 >= 1 的整数")

    fobj, should_close = _open_source(source)
    try:
        expected = _expected_size(fobj)
        out = bytearray()
        try:
            while True:
                start = len(out)
                # 容忍短读：循环读取直到凑满一块或到达 EOF。
                while len(out) - start < chunk_size:
                    piece = fobj.read(chunk_size - (len(out) - start))
                    if not piece:
                        break
                    out += piece
                if len(out) == start:
                    break
                if on_chunk is not None:
                    on_chunk(bytes(out[start:]), start)
        except OSError as exc:
            raise ChunkReadError(
                "读取失败: {}".format(exc),
                bytes_read=len(out),
                partial_data=bytes(out),
            ) from exc

        if expected is not None and len(out) < expected:
            raise ChunkReadError(
                "文件在读取过程中被截断: 期望 {} 字节, 实际读到 {} 字节".format(
                    expected, len(out)
                ),
                bytes_read=len(out),
                partial_data=bytes(out),
            )
        return bytes(out)
    finally:
        if should_close:
            fobj.close()


def iter_chunks(source, chunk_size):
    """生成器形式的分块读取，逐块产出 (offset, chunk)。

    语义与 read_in_chunks 相同：不完整末块会被产出；中途截断 / IO
    错误抛出 ChunkReadError（bytes_read 指出出错前已读字节数）。
    """
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
        raise ValueError("chunk_size 必须是 >= 1 的整数")

    fobj, should_close = _open_source(source)
    try:
        expected = _expected_size(fobj)
        offset = 0
        try:
            while True:
                parts = []
                got = 0
                while got < chunk_size:
                    piece = fobj.read(chunk_size - got)
                    if not piece:
                        break
                    parts.append(piece)
                    got += len(piece)
                    offset += len(piece)
                if got == 0:
                    break
                yield offset - got, b"".join(parts)
        except OSError as exc:
            raise ChunkReadError(
                "读取失败: {}".format(exc), bytes_read=offset
            ) from exc

        if expected is not None and offset < expected:
            raise ChunkReadError(
                "文件在读取过程中被截断: 期望 {} 字节, 实际读到 {} 字节".format(
                    expected, offset
                ),
                bytes_read=offset,
            )
    finally:
        if should_close:
            fobj.close()
