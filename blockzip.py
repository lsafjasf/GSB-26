"""blockzip: 支持随机访问的分块压缩存储库（仅标准库）。

文件格式（小端）：
  Header（29 字节）:
    magic        4s   = b"BZLK"
    version      B    = 1
    block_size   I    解压后每块大小（最后一块可不足）
    block_count  I
    total_size   Q    原始数据总字节数
    index_offset Q    块索引在文件中的起始偏移
  Data:
    block_count 个 zlib 压缩块，紧密排列
  Index（每项 20 字节，共 block_count 项）:
    comp_offset  Q    压缩块在文件中的偏移
    comp_size    I    压缩块字节数
    raw_size     I    该块解压后字节数
    crc32        I    解压后数据的 CRC32
"""

import io
import os
import struct
import zlib

MAGIC = b"BZLK"
VERSION = 1
HEADER = struct.Struct("<4sBIIQQ")
INDEX_ENTRY = struct.Struct("<QIII")
DEFAULT_BLOCK_SIZE = 64 * 1024


class BlockZipError(Exception):
    pass


class ChecksumError(BlockZipError):
    """块校验失败，携带出错的块下标。"""

    def __init__(self, block_index):
        self.block_index = block_index
        super().__init__(f"checksum mismatch in block {block_index}")


class RangeError(BlockZipError):
    pass


class ReadStats:
    """累计读取统计：证明随机读只解压了涉及的块。"""

    def __init__(self):
        self.read_calls = 0
        self.blocks_decompressed = 0
        self.bytes_decompressed = 0
        self.bytes_requested = 0

    def snapshot(self):
        return (self.read_calls, self.blocks_decompressed,
                self.bytes_decompressed, self.bytes_requested)

    def __repr__(self):
        return (f"ReadStats(calls={self.read_calls}, "
                f"blocks={self.blocks_decompressed}, "
                f"decompressed={self.bytes_decompressed}B, "
                f"requested={self.bytes_requested}B)")


def compress(data: bytes, block_size: int = DEFAULT_BLOCK_SIZE) -> bytes:
    """把 data 分块压缩，返回完整的 blockzip 文件字节串。"""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    blocks = [data[i:i + block_size]
              for i in range(0, len(data), block_size)]
    index = []
    out = io.BytesIO()
    out.write(b"\x00" * HEADER.size)  # 头部占位，最后回填
    for blk in blocks:
        comp = zlib.compress(blk, 6)
        index.append((out.tell(), len(comp), len(blk),
                      zlib.crc32(blk) & 0xFFFFFFFF))
        out.write(comp)
    index_offset = out.tell()
    for entry in index:
        out.write(INDEX_ENTRY.pack(*entry))
    header = HEADER.pack(MAGIC, VERSION, block_size, len(blocks),
                         len(data), index_offset)
    out.seek(0)
    out.write(header)
    return out.getvalue()


def compress_file(src_path: str, dst_path: str,
                  block_size: int = DEFAULT_BLOCK_SIZE) -> None:
    with open(src_path, "rb") as f:
        data = f.read()
    with open(dst_path, "wb") as f:
        f.write(compress(data, block_size))


class Reader:
    """随机访问读取器。read() 只解压与范围相交的块。"""

    def __init__(self, source):
        """source: 文件路径，或完整的文件字节串。"""
        if isinstance(source, (bytes, bytearray)):
            self._f = io.BytesIO(bytes(source))
            self._close = False
        else:
            self._f = open(source, "rb")
            self._close = True
        header = self._f.read(HEADER.size)
        if len(header) < HEADER.size:
            raise BlockZipError("file too small for header")
        magic, version, self.block_size, self.block_count, \
            self.total_size, self._index_offset = HEADER.unpack(header)
        if magic != MAGIC:
            raise BlockZipError("bad magic")
        if version != VERSION:
            raise BlockZipError(f"unsupported version {version}")
        self._index = []
        self._f.seek(self._index_offset)
        for _ in range(self.block_count):
            raw = self._f.read(INDEX_ENTRY.size)
            if len(raw) < INDEX_ENTRY.size:
                raise BlockZipError("truncated index")
            self._index.append(INDEX_ENTRY.unpack(raw))
        self.stats = ReadStats()

    def close(self):
        if self._close:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __len__(self):
        return self.total_size

    def _decompress_block(self, i):
        comp_offset, comp_size, raw_size, crc = self._index[i]
        self._f.seek(comp_offset)
        comp = self._f.read(comp_size)
        if len(comp) < comp_size:
            raise BlockZipError(f"truncated data in block {i}")
        try:
            raw = zlib.decompress(comp)
        except zlib.error:
            raise ChecksumError(i) from None
        if len(raw) != raw_size:
            raise BlockZipError(f"size mismatch in block {i}")
        if zlib.crc32(raw) & 0xFFFFFFFF != crc:
            raise ChecksumError(i)
        self.stats.blocks_decompressed += 1
        self.stats.bytes_decompressed += raw_size
        return raw

    def read(self, offset: int, length: int) -> bytes:
        """读取 [offset, offset+length)。越界抛 RangeError。"""
        if offset < 0 or length < 0 or offset + length > self.total_size:
            raise RangeError(
                f"read({offset}, {length}) out of range "
                f"[0, {self.total_size})")
        if length == 0:
            return b""
        self.stats.read_calls += 1
        self.stats.bytes_requested += length
        first = offset // self.block_size
        last = (offset + length - 1) // self.block_size
        parts = []
        for i in range(first, last + 1):
            raw = self._decompress_block(i)
            start = max(offset - i * self.block_size, 0)
            end = min(offset + length - i * self.block_size,
                      self.block_size)
            parts.append(raw[start:end])
        return b"".join(parts)

    def read_all(self) -> bytes:
        return self.read(0, self.total_size)
