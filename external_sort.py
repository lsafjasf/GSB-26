"""外部排序：分块排序落盘 + 多轮 k 路归并。稳定、守恒、异常安全、可降级。

性质：
  - 守恒：输出条数 == 输入条数（不丢不重）；
  - 稳定：相同键的记录保持输入中的相对顺序；
  - 异常安全：任何异常路径（包括多轮归并中途异常）都不在临时目录残留 run 文件；
  - 可降级：临时目录不可写时，可改用纯内存归并，或抛出明确的
    TempDirUnavailableError（降级路径结果与磁盘路径逐字节一致，
    见 test_resource_governance 的对拍用例）。

资源治理的两条轴：
  - chunk_size（块大小）：分块排序缓冲区条数，主导内存峰值；
  - merge_ways（归并路数）：每轮归并同时打开的 run 数 / 堆中元素数，
    同时限制文件句柄与归并缓冲区内存；run 数超过 merge_ways 时自动多轮归并。

归并最小键选取与稳定性：
  堆元素为 (key, run_idx, line, stream)。key 相同则比较 run_idx，
  run_idx 小的 run 在输入中位置更早，因此并列时总是先弹出更早 run 的记录；
  同一 run 内部由 list.sort（稳定）保证相对顺序。两者合起来保证全局稳定。
  不变量（_merge_streams 内有断言）：弹出的 key 序列单调不减。

内存：峰值 ≈ chunk_size 条记录（分块缓冲区）+ merge_ways 条记录
（归并堆，每路一条，外加每路一个文件读缓冲），即
O(chunk_size + merge_ways)；多轮归并不改变该上界。
"""

import heapq
import os
import tempfile
from collections import namedtuple

POLICY_MEMORY = "memory"  # 临时目录不可写 -> 降级为内存归并
POLICY_FAIL = "fail"      # 临时目录不可写 -> 抛 TempDirUnavailableError

_VALID_POLICIES = (POLICY_MEMORY, POLICY_FAIL)


class TempDirUnavailableError(OSError):
    """临时目录不可写（无法创建临时目录或写入 run 文件）。

    是 OSError 的子类：既可按明确的降级失败类型捕获，
    也兼容只捕获 OSError 的旧调用方。
    """


# 排序执行信息（资源治理可观测性）：
#   backend:      "disk" 或 "memory"
#   num_runs:     磁盘路径首轮切出的 run 数（内存路径为 0）
#   merge_rounds: 归并轮数（0 = 单 run；run>1 时至少 1 轮）
#   merge_ways:   实际归并路数上限
SortResult = namedtuple("SortResult",
                        "backend num_runs merge_rounds merge_ways")


def external_sort(input_path, output_path, key, chunk_size=10000,
                  temp_dir=None, merge_ways=8,
                  on_temp_error=POLICY_FAIL):
    """对 input_path 的文本记录按 key 稳定排序，结果写入 output_path。

    chunk_size:    每块最多容纳的记录条数（决定分块缓冲区内存，必须 >= 1）。
    temp_dir:      临时 run 文件的父目录；None 表示系统默认临时目录。
    merge_ways:    每轮归并的最大路数（>=2），run 更多时自动多轮归并。
                   同时限制同时打开的 run 文件句柄数与归并缓冲区内存。
    on_temp_error: 临时目录/run 文件不可写（OSError）时的策略：
                   "memory" - 降级为纯内存稳定排序，结果与磁盘路径一致；
                   "fail"   - 抛 TempDirUnavailableError（默认），不留下临时文件。

    临时 run 文件由 TemporaryDirectory 托管；每轮归并消费完的中间 run
    立即显式删除，异常路径由 TemporaryDirectory 兜底清理，因此归并中途
    抛异常时也不会残留任何临时文件。最终输出文件本身不可写时抛出原始
    OSError（由调用方处理），不会误报为“临时目录不可用”。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if merge_ways < 2:
        raise ValueError("merge_ways must be >= 2")
    if on_temp_error not in _VALID_POLICIES:
        raise ValueError(
            "on_temp_error must be one of %r" % (_VALID_POLICIES,))
    try:
        return _external_sort_disk(input_path, output_path, key,
                                   chunk_size, temp_dir, merge_ways)
    except TempDirUnavailableError:
        if on_temp_error == POLICY_MEMORY:
            return _in_memory_sort(input_path, output_path, key, merge_ways)
        raise


def _external_sort_disk(input_path, output_path, key,
                        chunk_size, temp_dir, merge_ways):
    try:
        td_cm = tempfile.TemporaryDirectory(prefix="extsort_", dir=temp_dir)
    except OSError as exc:
        raise TempDirUnavailableError(
            "cannot create temp directory under %r: %s"
            % (temp_dir, exc)) from exc
    with td_cm as work:
        run_paths = _split(input_path, key, chunk_size, work)
        num_runs = len(run_paths)
        rounds = 0
        # 中间轮：归并产物仍写在 work 内；run 数 <= merge_ways 后进入最终轮。
        while len(run_paths) > merge_ways:
            rounds += 1
            run_paths = _merge_round(run_paths, key, merge_ways, work, rounds)
        # 最终轮直接写入 output_path（避免跨文件系统 rename/EXDEV），
        # 此处的 OSError 是输出侧错误，原样上抛，不触发降级误判。
        if not run_paths:
            with open(output_path, "w", encoding="utf-8"):
                pass
        elif len(run_paths) == 1:
            _copy_stream(run_paths[0], output_path)
        else:
            rounds += 1
            _merge_streams(run_paths, output_path, key)
    return SortResult("disk", num_runs, rounds, merge_ways)


def _split(input_path, key, chunk_size, work):
    run_paths = []
    buf = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            buf.append(line)
            if len(buf) >= chunk_size:
                run_paths.append(_flush_run(buf, key, work, len(run_paths)))
                buf.clear()
    if buf:
        run_paths.append(_flush_run(buf, key, work, len(run_paths)))
    return run_paths


def _flush_run(buf, key, work, idx):
    buf.sort(key=key)  # list.sort 稳定：块内相同键保持输入相对顺序
    path = os.path.join(work, "run_%06d.run" % idx)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(buf)
    except OSError as exc:
        raise TempDirUnavailableError(
            "cannot write temp run %r: %s" % (path, exc)) from exc
    return path


def _merge_round(run_paths, key, merge_ways, work, round_idx):
    """一轮中间归并：按 merge_ways 分组，每组归并为 work 内的新 run；
    单 run 组直通（不产生新文件）。该轮成功后删除被消费的旧 run。"""
    next_paths = []
    consumed = []
    try:
        for group_idx, start in enumerate(range(0, len(run_paths), merge_ways)):
            group = run_paths[start:start + merge_ways]
            if len(group) == 1:
                next_paths.append(group[0])  # 直通，留给后续轮次
                continue
            merged = os.path.join(work, "run_%06d_%06d.run"
                                  % (round_idx, group_idx))
            _merge_streams(group, merged, key)
            next_paths.append(merged)
            consumed.extend(group)
    except TempDirUnavailableError:
        raise
    except OSError as exc:
        # 中间 run 的读写都属于临时存储：转换为明确的降级触发信号。
        raise TempDirUnavailableError(
            "temp merge failed: %s" % exc) from exc
    # 该轮成功结束后立即删除被消费的上一轮 run（已并入新 run），
    # 把磁盘占用控制在“一轮”的量级；任何异常路径由 TemporaryDirectory 兜底。
    for path in consumed:
        if os.path.exists(path):
            os.unlink(path)
    return next_paths


def _copy_stream(src_path, dst_path):
    with open(src_path, encoding="utf-8") as src, \
            open(dst_path, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)


def _merge_streams(run_paths, output_path, key):
    """把一组有序 run 归并写入 output_path。"""
    streams = [open(p, encoding="utf-8") for p in run_paths]
    try:
        heap = []
        for run_idx, stream in enumerate(streams):
            line = stream.readline()
            if line:
                # (key, run_idx) 唯一确定堆序：key 并列时 run_idx 小者先出，
                # 即输入位置更早的记录先出 -> 跨 run 稳定。
                heap.append((key(line), run_idx, line, stream))
        heapq.heapify(heap)
        last_key = None
        with open(output_path, "w", encoding="utf-8") as out:
            while heap:
                k, run_idx, line, stream = heapq.heappop(heap)
                # 不变量：归并输出的键单调不减。
                assert last_key is None or not k < last_key, \
                    "merge invariant violated"
                last_key = k
                out.write(line)
                nxt = stream.readline()
                if nxt:
                    heapq.heappush(heap, (key(nxt), run_idx, nxt, stream))
    finally:
        for stream in streams:
            stream.close()


def _in_memory_sort(input_path, output_path, key, merge_ways):
    """降级路径：纯内存稳定排序，不使用任何临时文件。

    输出与磁盘路径逐字节一致（同为 Python 稳定排序，换行原样保留），
    对拍见 test_resource_governance。
    """
    with open(input_path, encoding="utf-8") as f:
        records = f.readlines()
    records.sort(key=key)
    with open(output_path, "w", encoding="utf-8") as out:
        out.writelines(records)
    return SortResult("memory", 0, 0, merge_ways)
