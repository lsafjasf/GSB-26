"""查询执行器：串行基线 + 分片并行（仅标准库 Python 3）。

查询模型（管道）：scan -> filter -> project，或 scan -> filter -> aggregate。

并行执行把输入行切成若干**连续、有序**的分片（shard），每个 worker
独立扫描一个分片并返回：
  - 明细查询：该分片过滤/投影后的行（保持片内顺序）
  - 聚合查询：该分片的局部聚合状态（partial aggregate state）

归并阶段严格按分片下标 0,1,2,... 拼接/合并，因此：
  - 明细结果的行顺序与把全部行交给串行执行器**逐行一致**；
  - 聚合结果的合并口径与“把各分片按顺序接成一整段再聚合”等价。

聚合口径（跨分片边界）
----------------------
- count()                全行数；count(col) 统计 col 非 None 的行；
                         合并 = 各分片计数直接相加。
- sum(col)               对非 None 数值求和；合并 = 各分片部分和依次相加。
                         整数完全精确且与顺序无关；float 因浮点结合律不保证
                         按位一致（本项目对拍数据均为整数）。
- min(col) / max(col)    对非 None 值取最小/最大；空结果为 None；
                         合并 = 各分片最值再取 min/max（结合/交换律安全）。
- distinct(col)          对非 None 值去重；合并 = 各分片集合取并集，
                         输出 sorted(并集)（确定性顺序），并给出去重计数。
"""

from __future__ import annotations

import multiprocessing as mp
import operator
import os
from dataclasses import dataclass
from typing import Any, Sequence

Row = dict[str, Any]


@dataclass(frozen=True)
class Filter:
    column: str
    op: str  # eq / ne / lt / le / gt / ge
    value: Any


@dataclass(frozen=True)
class Aggregate:
    func: str  # count / sum / min / max / distinct
    column: str | None = None


@dataclass(frozen=True)
class Query:
    filter: Filter | None = None
    projection: tuple[str, ...] | None = None  # None = 返回全部列
    aggregates: tuple[Aggregate, ...] = ()

    def is_aggregate(self) -> bool:
        return bool(self.aggregates)


_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}

_AGG_FUNCS = ("count", "sum", "min", "max", "distinct")


def _validate(query: Query) -> None:
    if query.filter is not None and query.filter.op not in _OPS:
        raise ValueError(f"unsupported filter op: {query.filter.op!r}")
    for agg in query.aggregates:
        if agg.func not in _AGG_FUNCS:
            raise ValueError(f"unsupported aggregate: {agg.func!r}")
        if agg.func != "count" and agg.column is None:
            raise ValueError(f"{agg.func} requires a column")
        if agg.func == "count" and query.projection is not None:
            raise ValueError("aggregate query must not set projection")
    if query.aggregates and query.projection is not None:
        raise ValueError("projection and aggregates are mutually exclusive")


def _match(row: Row, filt: Filter | None) -> bool:
    if filt is None:
        return True
    return _OPS[filt.op](row[filt.column], filt.value)


def _project(row: Row, projection: tuple[str, ...] | None) -> Row:
    if projection is None:
        return dict(row)
    return {col: row[col] for col in projection}


def _agg_name(agg: Aggregate) -> str:
    return agg.func if agg.column is None else f"{agg.func}_{agg.column}"


def _init_state(func: str) -> Any:
    if func in ("count", "sum"):
        return 0
    if func in ("min", "max"):
        return None
    if func == "distinct":
        return set()
    raise AssertionError(func)


def _update_state(func: str, column: str | None, state: Any, row: Row) -> Any:
    if func == "count":
        if column is None or row[column] is not None:
            return state + 1
        return state
    value = row[column]
    if value is None:
        return state
    if func == "sum":
        return state + value
    if func == "min":
        return value if state is None or value < state else state
    if func == "max":
        return value if state is None or value > state else state
    if func == "distinct":
        state.add(value)
        return state
    raise AssertionError(func)


# worker 函数必须在模块顶层，以便 pickle 到子进程
def _scan_partition(task: tuple[Sequence[Row], Query]) -> tuple[list[Row] | None,
                                                                list[Any]]:
    rows, query = task
    out_rows: list[Row] | None = [] if not query.is_aggregate() else None
    states = [_init_state(a.func) for a in query.aggregates]
    for row in rows:
        if not _match(row, query.filter):
            continue
        if out_rows is not None:
            out_rows.append(_project(row, query.projection))
        else:
            for i, agg in enumerate(query.aggregates):
                states[i] = _update_state(agg.func, agg.column, states[i], row)
    return out_rows, states


def _finalize(parts: list[tuple[list[Row] | None, list[Any]]],
              query: Query) -> list[Row]:
    if not query.is_aggregate():
        # 严格按分片顺序拼接，保证与串行逐行一致
        merged: list[Row] = []
        for rows, _ in parts:
            merged.extend(rows)
        return merged

    result: Row = {}
    for i, agg in enumerate(query.aggregates):
        partials = [states[i] for _, states in parts]
        if agg.func in ("count", "sum"):
            value: Any = 0
            for p in partials:
                value = value + p  # 整数精确；float 顺序口径=分片下标顺序
        elif agg.func == "min":
            nonnull = [p for p in partials if p is not None]
            value = min(nonnull) if nonnull else None
        elif agg.func == "max":
            nonnull = [p for p in partials if p is not None]
            value = max(nonnull) if nonnull else None
        else:  # distinct
            union: set = set()
            for p in partials:
                union |= p
            value = sorted(union)
            result[f"n_{_agg_name(agg)}"] = len(value)
        result[_agg_name(agg)] = value
    return [result]


def shard_bounds(n: int, k: int) -> list[tuple[int, int]]:
    """把 [0, n) 切成 k 个连续区间 [(start, end), ...]，负载尽量均衡。"""
    if k < 1:
        raise ValueError("k must be >= 1")
    if n == 0:
        return [(0, 0)]
    k = min(k, n)
    base, rem = divmod(n, k)
    bounds: list[tuple[int, int]] = []
    start = 0
    for i in range(k):
        size = base + (1 if i < rem else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


def shard_rows(rows: Sequence[Row], k: int) -> list[list[Row]]:
    """把行切成 k 个**连续**分片，负载尽量均衡（前 rem 片多 1 行）。

    k > 行数时尾部分片为空；空输入返回单个空分片。
    """
    return [list(rows[s:e]) for s, e in shard_bounds(len(rows), k)]


# fork 共享数据：fork 出的 worker 通过写时复制共享父进程内存中的行数据，
# 任务只需传输 (start, end) 下标，避免把整表 pickle 进进程池。
_FORK_ROWS: Sequence[Row] | None = None


def _scan_shared_range(task: tuple[int, int, Query]) -> tuple[list[Row] | None,
                                                            list[Any]]:
    start, end, query = task
    assert _FORK_ROWS is not None
    return _scan_partition((_FORK_ROWS[start:end], query))


def execute_serial(rows: Sequence[Row], query: Query) -> list[Row]:
    """串行基线：全部行在单线程内扫描一次。"""
    _validate(query)
    return _finalize([_scan_partition((rows, query))], query)


def execute_parallel(rows: Sequence[Row], query: Query,
                     workers: int | None = None,
                     partitions: Sequence[Sequence[Row]] | None = None
                     ) -> list[Row]:
    """分片并行执行。

    - partitions 为 None 时按 workers（默认 CPU 数）做连续均衡分片；
    - 传入 partitions 可构造倾斜/自定义分片（测试用）；
    - 只有 1 个分片时直接走串行基线，避免进程池开销；
    - 结果按分片下标顺序归并，与 execute_serial 逐行一致。
    """
    _validate(query)
    can_fork = "fork" in mp.get_all_start_methods()

    if partitions is None:
        k = workers or (os.cpu_count() or 1)
        bounds = shard_bounds(len(rows), k)
        if len(bounds) <= 1:
            return execute_serial(rows, query)
        if can_fork:
            # fork 路径：子进程共享父进程行数据（写时复制），任务只传下标
            global _FORK_ROWS
            _FORK_ROWS = rows
            try:
                with mp.get_context("fork").Pool(processes=len(bounds)) as pool:
                    parts = pool.map(
                        _scan_shared_range,
                        [(s, e, query) for s, e in bounds])
            finally:
                _FORK_ROWS = None
        else:
            # 无 fork（如 Windows spawn）：退化为把分片数据随任务 pickle
            tasks = [(rows[s:e], query) for s, e in bounds]
            with mp.get_context().Pool(processes=len(bounds)) as pool:
                parts = pool.map(_scan_partition, tasks)
        return _finalize(parts, query)

    if len(partitions) <= 1:
        return execute_serial(
            partitions[0] if partitions else rows, query)
    tasks = [(part, query) for part in partitions]
    nproc = min(workers or len(tasks), len(tasks))
    ctx = mp.get_context("fork") if can_fork else mp.get_context()
    with ctx.Pool(processes=nproc) as pool:
        parts = pool.map(_scan_partition, tasks)  # map 保证按任务下标返回
    return _finalize(parts, query)


class ParallelExecutor:
    """长生命周期并行执行器：bind 一次数据，多次查询复用 worker 池。

    一次性 execute_parallel 每次查询都要新建进程池；fork 大内存父进程的
    页表复制是固定开销（实测 1 GiB RSS 约 0.16~0.28 s），小查询会被它
    吞掉收益。真实引擎里数据常驻、查询反复到来，因此本类在 bind() 时
    建池并把行数据通过 fork 写时复制共享给 worker，之后每次 execute()
    只传输下标与查询描述（几十字节），摊掉建池成本。

    结果口径与 execute_parallel / execute_serial 完全一致。
    """

    def __init__(self, workers: int | None = None):
        if "fork" not in mp.get_all_start_methods():
            raise RuntimeError("ParallelExecutor requires the fork start method")
        self.workers = workers or (os.cpu_count() or 1)
        self._pool: mp.pool.Pool | None = None
        self._bounds: list[tuple[int, int]] = []

    def bind(self, rows: Sequence[Row]) -> None:
        """绑定数据并（重）建 worker 池；重复调用会替换旧数据。"""
        self.close()
        global _FORK_ROWS
        _FORK_ROWS = rows
        self._bounds = shard_bounds(len(rows), self.workers)
        self._pool = mp.get_context("fork").Pool(
            processes=max(1, len(self._bounds)))

    def execute(self, query: Query) -> list[Row]:
        if self._pool is None:
            raise RuntimeError("call bind() before execute()")
        _validate(query)
        tasks = [(s, e, query) for s, e in self._bounds]
        parts = self._pool.map(_scan_shared_range, tasks)
        return _finalize(parts, query)

    def close(self) -> None:
        global _FORK_ROWS
        if self._pool is not None:
            self._pool.terminate()
            self._pool.join()
            self._pool = None
        _FORK_ROWS = None

    def __enter__(self) -> "ParallelExecutor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
