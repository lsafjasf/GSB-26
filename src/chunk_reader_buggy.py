"""分块读取大文件 —— 有缺陷的现网版本（仅用于复现问题，请勿使用）。

已知的四类缺陷：
  BUG 1: 读到（被截断的）文件末尾发生短读时，用上一块的多余字节补齐，
         导致上一块的尾部字节被重复处理。
  BUG 2: 读取过程中文件被截断 / 发生 IO 错误时，异常未做任何包装，
         原始 OSError 直接外抛，调用方无法区分、也拿不到已读字节数。
  BUG 3: 块偏移固定按 chunk_size 推进，发生短读（实际读到 n < chunk_size）
         后，后续所有块的偏移整体错位。
  BUG 4: 循环条件 offset + chunk_size <= size 直接跳过最后一个不完整块，
         不完整末块（以及小于一整块的整个文件）被静默丢弃。
"""

import os


def read_file_in_chunks(source, chunk_size):
    """按固定大小分块读取并返回全部内容（有缺陷）。

    source: 文件路径，或已打开的二进制文件对象（需支持 seek/tell/read）。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    if isinstance(source, (str, bytes, os.PathLike)):
        fobj = open(source, "rb")
        close = True
    else:
        fobj = source
        close = False

    try:
        fobj.seek(0, os.SEEK_END)
        size = fobj.tell()

        out = bytearray()
        prev = b""
        offset = 0
        # BUG 4: 余数部分（最后一个不完整块）永远不会进入循环。
        while offset + chunk_size <= size:
            fobj.seek(offset)
            # BUG 2: 未捕获 OSError；文件中途被截断时异常直接外抛。
            chunk = fobj.read(chunk_size)
            if len(chunk) < chunk_size:
                # BUG 1: 短读时用上一块的多余字节“补齐”，
                # 上一块尾部字节被重复处理。
                chunk = chunk + prev[len(chunk):]
            out += chunk
            prev = chunk
            # BUG 3: 应按实际读到的 len(chunk) 推进；
            # 短读后偏移与真实消费位置脱节，后续块整体错位。
            offset += chunk_size
        return bytes(out)
    finally:
        if close:
            fobj.close()
