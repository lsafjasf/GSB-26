"""blockzip: 支持随机访问的分块压缩存储库（仅标准库）。

文件格式（小端）::

    +------------------- 头部 (29 字节) -------------------+
    | magic      5B  "BZBX1"                               |
    | block_size 4B  解压块大小（最后一块可小于此值）       |
    | total_size 8B  原始数据总长度                         |
    | num_blocks 4B  块数量                                 |
    | index_off  8B  块索引区在文件中的偏移                 |
    +------------------- 数据区 ----------------------------+
    | block0 压缩数据 | block1 压缩数据 | ...              |
    +------------------- 索引区 (24B/块) -------------------+
    | comp_off 8B | comp_size 8B | raw_size 4B | crc32 4B  |
    +------------------------------------------------------+

随机读取时按索引只 seek + 解压命中的块，crc32 校验失败会定位到具体块号。
"""

from __future__ import annotations

import io
import os
import struct
import zlib
from dataclasses import dataclass, field

MAGIC = b"BZBX1"
VERSION = 1
DEFAULT_BLOCK_SIZE = 64 * 1024

_HEADER = struct.Struct("<5sIQIQ")          # magic, block_size, total_size, num_blocks, index_off
_INDEX_ENTRY = struct.Struct("<QQII")        # comp_off, comp_size, raw_size, crc32
HEADER_SIZE = _HEADER.size
INDEX_ENTRY_SIZE = _INDEX_ENTRY.size


class BlockZipError(Exception):
    """库的基础异常。"""


class FormatError(BlockZipError):
    """文件格式不合法。"""


class BlockChecksumError(BlockZipError):
    """某个块的 CRC32 校验失败，携带块号定位。"""

    def __init__(self, block_index: int, expected: int, actual: int):
        self.block_index = block_index
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"block {block_index} checksum mismatch: "
            f"expected crc32=0x{expected:08x}, got 0x{actual:08x}"
        )


class ReadOutOfRangeError(BlockZipError):
    """读取范围越界。"""


@dataclass
class BlockInfo:
    """块索引条目。"""

    comp_off: int      # 压缩数据在文件中的偏移
    comp_size: int     # 压缩后字节数
    raw_size: int      # 解压后字节数
    crc32: int         # 解压后数据的 CRC32


@dataclass
class ReadStats:
    """读取统计：累计解压字节量与块数，用于证明没有整文件解压。"""

    reads: int = 0
    blocks_decompressed: int = 0
    bytes_decompressed: int = 0   # 解压输出的字节量（含被裁剪掉的部分）
    bytes_requested: int = 0      # 调用方实际请求的字节量
    per_read: list = field(default_factory=list)  # (offset, length, blocks, bytes)

    def record(self, offset: int, length: int, blocks: int, decompressed: int) -> None:
        self.reads += 1
        self.blocks_decompressed += blocks
        self.bytes_decompressed += decompressed
        self.bytes_requested += length
        self.per_read.append((offset, length, blocks, decompressed))

    def reset(self) -> None:
        self.reads = 0
        self.blocks_decompressed = 0
        self.bytes_decompressed = 0
        self.bytes_requested = 0
        self.per_read.clear()

    def summary(self) -> str:
        return (
            f"reads={self.reads} blocks_decompressed={self.blocks_decompressed} "
            f"bytes_decompressed={self.bytes_decompressed} "
            f"bytes_requested={self.bytes_requested}"
        )


def compress(data: bytes, block_size: int = DEFAULT_BLOCK_SIZE) -> bytes:
    """把 data 压缩为 blockzip 格式字节串。"""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    out = io.BytesIO()
    out.write(b"\x00" * HEADER_SIZE)  # 头部占位，最后回填

    index: list[BlockInfo] = []
    for start in range(0, len(data), block_size):
        chunk = data[start : start + block_size]
        comp = zlib.compress(chunk, 6)
        index.append(
            BlockInfo(
                comp_off=out.tell(),
                comp_size=len(comp),
                raw_size=len(chunk),
                crc32=zlib.crc32(chunk) & 0xFFFFFFFF,
            )
        )
        out.write(comp)

    index_off = out.tell()
    for info in index:
        out.write(_INDEX_ENTRY.pack(info.comp_off, info.comp_size, info.raw_size, info.crc32))

    header = _HEADER.pack(MAGIC, block_size, len(data), len(index), index_off)
    blob = out.getvalue()
    return header + blob[HEADER_SIZE:]


def compress_file(src_path: str, dst_path: str, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
    with open(src_path, "rb") as f:
        data = f.read()
    with open(dst_path, "wb") as f:
        f.write(compress(data, block_size))


class BlockReader:
    """对 blockzip 数据做随机访问读取。"""

    def __init__(self, source, stats: ReadStats | None = None):
        """source: 文件路径、bytes、或二进制文件对象。"""
        self._owns_file = False
        if isinstance(source, (str, os.PathLike)):
            self._fp = open(source, "rb")
            self._owns_file = True
        elif isinstance(source, (bytes, bytearray)):
            self._fp = io.BytesIO(bytes(source))
        else:
            self._fp = source

        magic, self.block_size, self.total_size, num_blocks, index_off = self._read_header()
        if magic != MAGIC:
            raise FormatError(f"bad magic {magic!r}, not a blockzip file")
        self.index: list[BlockInfo] = []
        self._fp.seek(index_off)
        for _ in range(num_blocks):
            comp_off, comp_size, raw_size, crc = _INDEX_ENTRY.unpack(
                self._read_exact(_INDEX_ENTRY.size, at=None)
            )
            self.index.append(BlockInfo(comp_off, comp_size, raw_size, crc))
        self.stats = stats if stats is not None else ReadStats()

    def _read_header(self):
        self._fp.seek(0)
        raw = self._fp.read(HEADER_SIZE)
        if len(raw) != HEADER_SIZE:
            raise FormatError("file too small for header")
        return _HEADER.unpack(raw)

    def _read_exact(self, n: int, at: int | None) -> bytes:
        if at is not None:
            self._fp.seek(at)
        buf = self._fp.read(n)
        if len(buf) != n:
            raise FormatError("unexpected end of file")
        return buf

    def __len__(self) -> int:
        return self.total_size

    def close(self) -> None:
        if self._owns_file:
            self._fp.close()

    def __enter__(self) -> "BlockReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _decompress_block(self, i: int) -> bytes:
        info = self.index[i]
        comp = self._read_exact(info.comp_size, at=info.comp_off)
        try:
            raw = zlib.decompress(comp)
        except zlib.error as e:
            raise BlockChecksumError(i, info.crc32, -1) from e
        if len(raw) != info.raw_size:
            raise BlockChecksumError(i, info.crc32, -1)
        actual = zlib.crc32(raw) & 0xFFFFFFFF
        if actual != info.crc32:
            raise BlockChecksumError(i, info.crc32, actual)
        return raw

    def read(self, offset: int, length: int) -> bytes:
        """读取 [offset, offset+length)，只解压涉及的块。"""
        if offset < 0 or length < 0 or offset + length > self.total_size:
            raise ReadOutOfRangeError(
                f"read({offset}, {length}) out of range, total_size={self.total_size}"
            )
        if length == 0:
            self.stats.record(offset, 0, 0, 0)
            return b""

        first = offset // self.block_size
        last = (offset + length - 1) // self.block_size
        parts = []
        decompressed = 0
        for i in range(first, last + 1):
            raw = self._decompress_block(i)
            decompressed += len(raw)
            parts.append(raw)
        blob = b"".join(parts)
        cut = offset - first * self.block_size
        result = blob[cut : cut + length]
        self.stats.record(offset, length, last - first + 1, decompressed)
        return result
