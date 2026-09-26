"""外部排序（有缺陷的原始实现，仅用于复现线上问题，请勿修复本文件）。

流程：分块排序落盘 -> 多路归并。
已知缺陷（见 BUG 标注）：
  1. 归并时相同键的记录被当作重复丢弃；
  2. 异常路径不清理临时文件；
  3. 块边界处的记录被重复写出；
  4. 声称稳定，但归并并列时选取了更晚的 run，实际不稳定。
"""

import os


def external_sort_buggy(input_path, output_path, key, chunk_size=1000, workdir="."):
    """对 input_path 的文本记录按 key 排序，结果写入 output_path。声称稳定。"""
    run_paths = _split(input_path, key, chunk_size, workdir)
    _merge(run_paths, output_path, key)
    # BUG2: 只有正常走完才清理；_split/_merge 抛异常时临时文件残留在 workdir。
    for p in run_paths:
        os.unlink(p)


def _split(input_path, key, chunk_size, workdir):
    run_paths = []
    buf = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            buf.append(line)
            if len(buf) >= chunk_size:
                run_paths.append(_flush(buf, key, workdir, len(run_paths)))
                # BUG3: 保留了上一条记录，块边界记录会被重复写进下一块。
                buf = buf[-1:]
    if buf:
        run_paths.append(_flush(buf, key, workdir, len(run_paths)))
    return run_paths


def _flush(buf, key, workdir, idx):
    buf.sort(key=key)
    path = os.path.join(workdir, "extsort_tmp_%d_%d.run" % (os.getpid(), idx))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(buf)
    return path


def _merge(run_paths, output_path, key):
    files = [open(p, encoding="utf-8") for p in run_paths]
    heads = [_read_head(f, None, key) for f in files]
    out = open(output_path, "w", encoding="utf-8")
    while True:
        best = -1
        for i, h in enumerate(heads):
            if h is None:
                continue
            # BUG4: 并列时取“更晚的 run”（<=），相同键的相对顺序被颠倒，不稳定。
            if best == -1 or h[0] <= heads[best][0]:
                best = i
        if best == -1:
            break
        k, line = heads[best]
        out.write(line)
        heads[best] = _read_head(files[best], k, key)
    out.close()
    for f in files:
        f.close()


def _read_head(f, prev_k, key):
    # BUG1: 与上一条键相同的记录被当作“重复数据”跳过，相同键的记录在归并时丢失。
    while True:
        line = f.readline()
        if not line:
            return None
        k = key(line)
        if prev_k is None or k != prev_k:
            return [k, line]
