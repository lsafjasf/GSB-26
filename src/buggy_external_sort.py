"""旧版外部排序实现，刻意保留现网四类缺陷，仅用于回归测试的复现对照。

缺陷清单：
  1. 归并用 dict 按 key 暂存各块当前记录，相同键互相覆盖 -> 同键记录丢失。
  2. 临时文件直接落在工作目录，且只在正常结束时删除 -> 异常后残留。
  3. 分块循环把块边界记录 carry 到下一块再写一次 -> 边界记录重复。
  4. 块内用 chunk.sort() 按整行字典序排序 -> 相同键按记录内容重排，不稳定。
"""

import os


def buggy_external_sort(input_path, output_path, chunk_size=1000, temp_dir=None):
    work = temp_dir or os.getcwd()
    key_of = lambda r: r.split("\t", 1)[0]

    # ---- 阶段一：分块排序落盘 ----
    chunk_paths = []
    with open(input_path, "r", encoding="utf-8") as src:
        carry = None
        while True:
            chunk = []
            if carry is not None:
                chunk.append(carry)
                carry = None
            read_any = False
            while len(chunk) <= chunk_size:  # 缺陷3a: '<=' 多读一条
                line = src.readline()
                if not line:
                    break
                chunk.append(line)
                read_any = True
            if not chunk:
                break
            if read_any and len(chunk) > 1:
                carry = chunk[-1]  # 缺陷3b: 边界记录留给下一块重复写出
            chunk.sort()  # 缺陷4: 按整行排序，相同键被记录内容重排
            path = os.path.join(work, "buggy-chunk-%06d.tmp" % len(chunk_paths))
            with open(path, "w", encoding="utf-8") as out:
                out.writelines(chunk)
            chunk_paths.append(path)
            if not read_any:
                break

    # ---- 阶段二：多路归并（缺陷1：dict 按 key 覆盖同键记录）----
    readers = [open(p, "r", encoding="utf-8") for p in chunk_paths]
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

    # 缺陷2: 只有走到这里才清理；上面任何一步抛异常都会残留临时文件。
    for path in chunk_paths:
        os.remove(path)
