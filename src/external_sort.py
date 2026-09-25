"""外部排序：分块排序落盘 + 多路归并。

设计要点（对应现网四类缺陷的修复）：

1. 归并不丢记录：归并堆中的元素是 ``(key, chunk_index, record)`` 三元组，
   每个读取器（chunk 文件）在堆中始终至多占一个槽位，弹出一条就立即从
   同一读取器补一条，不存在用 dict 按 key 覆盖同键记录的问题。

2. 异常不残留临时文件：所有临时 chunk 文件都写入由 ``tempfile.mkdtemp``
   创建的专属目录，整个排序过程包在 ``try/finally`` 中，``finally`` 里
   无条件 ``shutil.rmtree``。即使归并中途抛异常，临时目录也会被清除。

3. 块边界不重复：分块用 ``for line in src`` 顺序消费输入迭代器，每条记录
   恰好进入一个块，不存在"边界记录带入下一块"的进位逻辑。

4. 真正的稳定性：块内用 ``list.sort(key=...)``（Python 保证稳定），块间
   归并时堆元素第二维是 ``chunk_index``——块编号单调对应记录在原始输入中
   的出现先后。相同键比较时由 ``chunk_index`` 决定先后，永不退化为比较
   记录内容，因此相同键的记录严格按原始输入相对顺序输出。

记录格式：每行一条记录，默认键为第一个制表符之前的字段（可用 key_func 覆盖）。
"""

from __future__ import annotations

import heapq
import os
import shutil
import tempfile
from typing import Callable, Iterator, List, Optional

DEFAULT_CHUNK_SIZE = 10_000


def default_key(record: str) -> str:
    """默认键函数：取第一个 TAB 之前的字段。"""
    return record.split("\t", 1)[0]


def external_sort(
    input_path: str,
    output_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    temp_dir: Optional[str] = None,
    key_func: Optional[Callable[[str], object]] = None,
) -> int:
    """对 ``input_path`` 做稳定外部排序，结果写入 ``output_path``。

    返回写出的记录条数。任何异常都会清理全部临时文件后再向上抛出。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    key = key_func or default_key

    # 专属临时目录：即使调用方与其他进程共享 temp_dir，也只清理自己的目录。
    work_dir = tempfile.mkdtemp(prefix="extsort-", dir=temp_dir)
    try:
        chunk_paths = _split_into_sorted_chunks(input_path, work_dir, chunk_size, key)
        written = _merge_chunks(chunk_paths, output_path, key)
        return written
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _split_into_sorted_chunks(
    input_path: str, work_dir: str, chunk_size: int, key: Callable[[str], object]
) -> List[str]:
    """顺序读取输入，每 chunk_size 条排序落盘一次。每条记录恰好进入一个块。"""
    chunk_paths: List[str] = []
    with open(input_path, "r", encoding="utf-8") as src:
        chunk: List[str] = []
        for line in src:
            chunk.append(line)
            if len(chunk) >= chunk_size:
                chunk_paths.append(_flush_chunk(chunk, work_dir, len(chunk_paths), key))
                chunk = []
        if chunk:
            chunk_paths.append(_flush_chunk(chunk, work_dir, len(chunk_paths), key))
    return chunk_paths


def _flush_chunk(chunk: List[str], work_dir: str, index: int, key) -> str:
    # list.sort 是稳定排序：块内相同键保持输入相对顺序（不变量 I1）。
    chunk.sort(key=key)
    path = os.path.join(work_dir, "chunk-%06d.tmp" % index)
    with open(path, "w", encoding="utf-8") as out:
        out.writelines(chunk)
    return path


def _iter_lines(path: str) -> Iterator[str]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            yield line


def _merge_chunks(chunk_paths: List[str], output_path: str, key) -> int:
    """k 路归并。返回写出条数。

    最小键选取：堆元素为 ``(key, chunk_index, record)``，heapq 弹出
    元组最小者，即键最小者；键相同则由 chunk_index（= 原始输入中的块序）
    决定，块内顺序在落盘时已由稳定排序保证（不变量 I2）。
    """
    written = 0
    with open(output_path, "w", encoding="utf-8") as out:
        if not chunk_paths:
            return 0
        readers = [_iter_lines(p) for p in chunk_paths]
        heap: List[tuple] = []
        for idx, reader in enumerate(readers):
            first = next(reader, None)
            if first is not None:
                heapq.heappush(heap, (key(first), idx, first))
        while heap:
            _, idx, record = heapq.heappop(heap)
            out.write(record)
            written += 1
            nxt = next(readers[idx], None)
            if nxt is not None:
                heapq.heappush(heap, (key(nxt), idx, nxt))
    return written
