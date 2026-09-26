"""外部排序：分块排序落盘 + 多路归并。稳定、守恒、异常安全。

性质：
  - 守恒：输出条数 == 输入条数（不丢不重）；
  - 稳定：相同键的记录保持输入中的相对顺序；
  - 异常安全：任何异常路径都不在工作目录残留临时文件。

归并最小键选取与稳定性：
  堆元素为 (key, run_idx, line, file)。key 相同则比较 run_idx，
  run_idx 小的 run 在输入中位置更早，因此并列时总是先弹出更早 run 的记录；
  同一 run 内部由 list.sort（稳定）保证相对顺序。两者合起来保证全局稳定。
  不变量（merge 内有断言）：弹出的 key 序列单调不减。

内存：峰值 ≈ chunk_size 条记录（分块缓冲区）+ run 数量条记录（归并堆，
每 run 一条）。chunk_size 越大，run 越少，堆越小，但分块缓冲区线性增长，
故峰值内存由 chunk_size 主导，近似 O(chunk_size)。
"""

import heapq
import os
import tempfile


def external_sort(input_path, output_path, key, chunk_size=10000, temp_dir=None):
    """对 input_path 的文本记录按 key 稳定排序，结果写入 output_path。

    chunk_size: 每块最多容纳的记录条数（决定内存峰值，必须 >= 1）。
    temp_dir:   临时 run 文件的父目录；None 表示系统默认临时目录。
                临时目录由 TemporaryDirectory 托管，正常结束或异常都会清理。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    with tempfile.TemporaryDirectory(prefix="extsort_", dir=temp_dir) as work:
        run_paths = _split(input_path, key, chunk_size, work)
        _merge(run_paths, output_path, key)


def _split(input_path, key, chunk_size, work):
    run_paths = []
    buf = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            buf.append(line)
            if len(buf) >= chunk_size:
                run_paths.append(_flush(buf, key, work, len(run_paths)))
                buf.clear()  # 修复 bug3：块写盘后清空，边界记录不重复
    if buf:
        run_paths.append(_flush(buf, key, work, len(run_paths)))
    return run_paths


def _flush(buf, key, work, idx):
    buf.sort(key=key)  # list.sort 稳定：块内相同键保持输入相对顺序
    path = os.path.join(work, "run_%06d.run" % idx)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(buf)
    return path


def _merge(run_paths, output_path, key):
    files = [open(p, encoding="utf-8") for p in run_paths]
    try:
        heap = []
        for run_idx, f in enumerate(files):
            line = f.readline()
            if line:
                # (key, run_idx) 唯一确定堆序：key 并列时 run_idx 小者先出，
                # 即输入位置更早的记录先出 -> 跨 run 稳定。
                heap.append((key(line), run_idx, line, f))
        heapq.heapify(heap)
        last_key = None
        with open(output_path, "w", encoding="utf-8") as out:
            while heap:
                k, run_idx, line, f = heapq.heappop(heap)
                # 不变量：归并输出的键单调不减。
                assert last_key is None or not k < last_key, "merge invariant violated"
                last_key = k
                out.write(line)
                nxt = f.readline()
                if nxt:
                    heapq.heappush(heap, (key(nxt), run_idx, nxt, f))
    finally:
        for f in files:
            f.close()
