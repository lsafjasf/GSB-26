"""旧版外部排序实现，刻意保留现网四类缺陷，仅用于回归测试的复现对照。

缺陷清单（默认全部开启；每项都有独立开关，可逐项关闭以隔离复现）：
  1. lose_duplicate_keys=True       归并用 dict 按 key 暂存各块当前记录，
                                    相同键互相覆盖 -> 同键记录丢失，甚至整个
                                    读取器被静默丢弃。
  2. leave_temp_files_on_error=True 临时文件直接落在工作目录，且只在正常结束
                                    时删除 -> 异常后残留。
  3. duplicate_boundary=True        分块循环把块边界记录 carry 到下一块再写
                                    一次 -> 边界记录重复。
  4. unstable_within_chunk=True     块内用 chunk.sort() 按整行字典序排序
                                    -> 相同键按记录内容重排，不稳定。

注意缺陷之间会互相掩盖：缺陷3 在块边界制造出的重复键，会被缺陷1 的 dict
覆盖逻辑"去重"，最终输出里看不到重复（反而表现为更多记录丢失）；缺陷1/3
造成的记录丢失也会让缺陷4 的重排无法从输出中确认。因此要稳定复现某类
缺陷，应关闭其余三个开关，只保留待复现的那一项。
"""

import heapq
import os
import shutil
import tempfile


def buggy_external_sort(input_path, output_path, chunk_size=1000, temp_dir=None,
                        lose_duplicate_keys=True,
                        leave_temp_files_on_error=True,
                        duplicate_boundary=True,
                        unstable_within_chunk=True):
    key_of = lambda r: r.split("\t", 1)[0]

    if leave_temp_files_on_error:
        # 缺陷2: 临时文件散落工作目录，且只有正常走到最后才清理；
        # 中途任何一步抛异常都会残留临时文件。
        work = temp_dir or os.getcwd()
        chunk_paths = _split_into_chunks(
            input_path, work, chunk_size, key_of,
            duplicate_boundary, unstable_within_chunk)
        _merge_chunks(chunk_paths, output_path, key_of, lose_duplicate_keys)
        for path in chunk_paths:
            os.remove(path)
    else:
        # 对照路径：专属临时目录 + try/finally 无条件清理。
        work_dir = tempfile.mkdtemp(prefix="buggy-extsort-", dir=temp_dir)
        try:
            chunk_paths = _split_into_chunks(
                input_path, work_dir, chunk_size, key_of,
                duplicate_boundary, unstable_within_chunk)
            _merge_chunks(chunk_paths, output_path, key_of, lose_duplicate_keys)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


def _split_into_chunks(input_path, work, chunk_size, key_of,
                       duplicate_boundary, unstable_within_chunk):
    chunk_paths = []
    with open(input_path, "r", encoding="utf-8") as src:
        carry = None
        while True:
            chunk = []
            if carry is not None:
                chunk.append(carry)
                carry = None
            read_any = False
            # 缺陷3a: 缺陷开启时每块多读一条（原代码为 len(chunk) <= chunk_size）。
            limit = chunk_size + 1 if duplicate_boundary else chunk_size
            while len(chunk) < limit:
                line = src.readline()
                if not line:
                    break
                chunk.append(line)
                read_any = True
            if not chunk:
                break
            if duplicate_boundary and read_any and len(chunk) > 1:
                carry = chunk[-1]  # 缺陷3b: 边界记录留给下一块重复写出
            if unstable_within_chunk:
                chunk.sort()  # 缺陷4: 按整行排序，相同键被记录内容重排
            else:
                chunk.sort(key=key_of)
            path = os.path.join(work, "buggy-chunk-%06d.tmp" % len(chunk_paths))
            with open(path, "w", encoding="utf-8") as out:
                out.writelines(chunk)
            chunk_paths.append(path)
            if not read_any:
                break
    return chunk_paths


def _merge_chunks(chunk_paths, output_path, key_of, lose_duplicate_keys):
    readers = [open(p, "r", encoding="utf-8") for p in chunk_paths]
    if lose_duplicate_keys:
        # 缺陷1: dict 按 key 暂存各块当前记录，相同键互相覆盖。
        current = {}
        for i, reader in enumerate(readers):
            line = reader.readline()
            if line:
                current[key_of(line)] = (i, line)
        with open(output_path, "w", encoding="utf-8") as out:
            while current:
                k = min(current)
                i, line = current.pop(k)
                out.write(line)
                nxt = readers[i].readline()
                if nxt:
                    current[key_of(nxt)] = (i, nxt)
        for reader in readers:
            reader.close()
    else:
        # 对照路径：堆归并，每个读取器在堆中至多占一个槽位。
        try:
            with open(output_path, "w", encoding="utf-8") as out:
                heap = []
                for i, reader in enumerate(readers):
                    line = reader.readline()
                    if line:
                        heapq.heappush(heap, (key_of(line), i, line))
                while heap:
                    _, i, line = heapq.heappop(heap)
                    out.write(line)
                    nxt = readers[i].readline()
                    if nxt:
                        heapq.heappush(heap, (key_of(nxt), i, nxt))
        finally:
            for reader in readers:
                reader.close()
