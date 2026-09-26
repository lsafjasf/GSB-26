"""原始（有缺陷）的分块读取实现 —— 仅用于复现现网四类问题，勿用于生产。

已知缺陷：
1. 读到文件末尾发生短读时，把缓冲区里上一块的残留字节重复拼接。
2. 读取过程中文件被截断 / 底层 IO 出错时，异常未捕获直接抛出。
3. 跨块边界的偏移按 chunk_size 累加并强制 seek，短读时错位。
4. 文件大小不能被块大小整除时，最后一个不完整块被静默丢弃。
"""

import os


def read_file_in_chunks(source, chunk_size=4096):
    """按固定大小分块读取并拼接，返回 bytes。source 可以是路径或二进制文件对象。"""
    if isinstance(source, (str, bytes, os.PathLike)):
        f = open(source, "rb")
        should_close = True
    else:
        f = source
        should_close = False

    try:
        # 用文件大小推算块数：整除以外的余数部分（最后一个不完整块）被丢弃 —— 缺陷 4
        cur = f.tell()
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(cur, os.SEEK_SET)
        nchunks = size // chunk_size

        out = bytearray()
        buf = bytearray(chunk_size)
        offset = 0
        for _ in range(nchunks):
            # 底层 readinto 出错（如文件被截断、设备错误）时未做任何捕获 —— 缺陷 2
            n = f.readinto(buf)
            # 不切片直接拼接整个缓冲区：短读时把上一块的残留字节重复拼入 —— 缺陷 1
            out += buf
            # 偏移按 chunk_size 累加并强制 seek，忽略实际读到的 n —— 缺陷 3
            offset += chunk_size
            f.seek(offset, os.SEEK_SET)
        return bytes(out)
    finally:
        if should_close:
            f.close()
