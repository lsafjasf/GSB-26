"""结构化日志查询引擎：按时间分块存储 + 块级统计下推剪枝。

核心思想：
- 日志按写入顺序切分为若干块（chunk），每块在构建时预计算统计信息：
  每个字段的取值集合（低基数时）、数值/字符串的最小最大值、字段出现次数。
- 查询时先用过滤条件与块统计做"可行性判定"，不可能含结果的块直接跳过，
  只有可能命中的块才会被逐条扫描。
- `top N` 按块的最大 ts 倒序处理块，堆满 N 条且剩余块的 ts 上界
  严格小于当前第 N 名的 ts 时提前终止，无需扫描全部数据。
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .parser import Cond, Query, parse


# ---------------------------------------------------------------- 记录匹配

def _cmp(op: str, lhs: Any, rhs: Any) -> bool:
    """同类型比较；类型不一致时条件不命中（返回 False）。"""
    if isinstance(lhs, bool) or isinstance(rhs, bool):
        if type(lhs) is not type(rhs):
            return False
    elif isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
        pass
    elif type(lhs) is not type(rhs):
        return False
    if op == "=":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    raise ValueError(op)


def record_matches(record: Dict[str, Any], conds: Tuple[Cond, ...]) -> bool:
    """条件只作用于字段存在的记录；字段缺失时任何条件（含 !=）都不命中。"""
    for c in conds:
        if c.field not in record:
            return False
        if not _cmp(c.op, record[c.field], c.value):
            return False
    return True


# ---------------------------------------------------------------- 块统计

class FieldStats:
    """单个字段在一个块内的统计。"""
    __slots__ = ("present", "distinct", "num_min", "num_max", "str_min", "str_max", "_distinct_limit")

    def __init__(self, distinct_limit: int):
        self.present = 0
        self.distinct: Optional[set] = set()
        self.num_min: Optional[float] = None
        self.num_max: Optional[float] = None
        self.str_min: Optional[str] = None
        self.str_max: Optional[str] = None
        self._distinct_limit = distinct_limit

    def add(self, value: Any) -> None:
        self.present += 1
        if self.distinct is not None:
            self.distinct.add(value)
            if len(self.distinct) > self._distinct_limit:
                self.distinct = None  # 基数过高，放弃取值集合
        if isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            if self.num_min is None or value < self.num_min:
                self.num_min = value
            if self.num_max is None or value > self.num_max:
                self.num_max = value
        elif isinstance(value, str):
            if self.str_min is None or value < self.str_min:
                self.str_min = value
            if self.str_max is None or value > self.str_max:
                self.str_max = value


class Chunk:
    """一个日志块：记录列表 + 预计算统计。"""
    __slots__ = ("records", "stats", "ts_min", "ts_max")

    def __init__(self, records: List[Tuple[int, Dict[str, Any]]], distinct_limit: int):
        self.records = records  # [(ingest_index, record), ...]
        stats: Dict[str, FieldStats] = {}
        for _, rec in records:
            for k, v in rec.items():
                fs = stats.get(k)
                if fs is None:
                    fs = stats[k] = FieldStats(distinct_limit)
                fs.add(v)
        self.stats = stats
        ts_stats = stats.get("ts")
        self.ts_min = ts_stats.num_min if ts_stats else None
        self.ts_max = ts_stats.num_max if ts_stats else None

    # -------------- 下推判定：该块是否可能被某条件命中 --------------

    def _may_match_cond(self, cond: Cond) -> bool:
        fs = self.stats.get(cond.field)
        if fs is None:
            return False  # 块内不存在该字段，任何条件都不可能命中
        v = cond.value
        op = cond.op

        if op == "=":
            if fs.distinct is not None and v not in fs.distinct:
                return False
            if isinstance(v, (int, float)) and not isinstance(v, bool) and fs.num_min is not None:
                if v < fs.num_min or v > fs.num_max:
                    return False
            if isinstance(v, str) and fs.str_min is not None:
                if v < fs.str_min or v > fs.str_max:
                    return False
            return True

        if op == "!=":
            # 仅当块内该字段全部等于 v 时才能排除
            if (
                fs.distinct is not None
                and len(fs.distinct) == 1
                and v in fs.distinct
                and fs.present == len(self.records)
            ):
                return False
            return True

        # 范围条件：数值与字符串分别用各自的 min/max 判定
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = fs.num_min, fs.num_max
        elif isinstance(v, str):
            lo, hi = fs.str_min, fs.str_max
        else:
            return True
        if lo is None:
            return True  # 块内该字段没有同类型值，保守不排除
        if op == ">":
            return hi > v
        if op == ">=":
            return hi >= v
        if op == "<":
            return lo < v
        if op == "<=":
            return lo <= v
        return True

    def may_match(self, conds: Tuple[Cond, ...]) -> bool:
        return all(self._may_match_cond(c) for c in conds)


# ---------------------------------------------------------------- 存储与查询

@dataclass
class QueryResult:
    rows: Optional[List[Dict[str, Any]]] = None   # select / top 的结果
    counts: Optional[Dict[Any, int]] = None       # count by 的结果
    scanned_chunks: int = 0
    skipped_chunks: int = 0
    elapsed_ms: float = 0.0

    @property
    def total_chunks(self) -> int:
        return self.scanned_chunks + self.skipped_chunks


class LogStore:
    def __init__(self, chunk_size: int = 1000, distinct_limit: int = 256):
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须为正")
        self.chunk_size = chunk_size
        self.distinct_limit = distinct_limit
        self.chunks: List[Chunk] = []
        self._buffer: List[Tuple[int, Dict[str, Any]]] = []
        self._next_index = 0

    # -------------- 写入 --------------

    def ingest(self, records: Iterable[Dict[str, Any]]) -> None:
        for rec in records:
            if "ts" not in rec:
                raise ValueError("每条记录必须包含 ts 字段")
            self._buffer.append((self._next_index, dict(rec)))
            self._next_index += 1
            if len(self._buffer) >= self.chunk_size:
                self._flush()

    def _flush(self) -> None:
        if self._buffer:
            self.chunks.append(Chunk(self._buffer, self.distinct_limit))
            self._buffer = []

    def finalize(self) -> None:
        self._flush()

    # -------------- 查询 --------------

    def query(self, q) -> QueryResult:
        """带块级剪枝与 top-N 提前终止的查询。"""
        query = parse(q) if isinstance(q, str) else q
        start = time.perf_counter()
        if query.top is not None:
            result = self._query_top(query, prune=True)
        else:
            result = self._query_scan(query, prune=True)
        result.elapsed_ms = (time.perf_counter() - start) * 1000.0
        return result

    def full_scan(self, q) -> QueryResult:
        """参考实现：不剪枝、不提前终止，逐块逐条扫描。用于对拍。"""
        query = parse(q) if isinstance(q, str) else q
        start = time.perf_counter()
        if query.top is not None:
            result = self._query_top(query, prune=False, early_stop=False)
        else:
            result = self._query_scan(query, prune=False)
        result.elapsed_ms = (time.perf_counter() - start) * 1000.0
        return result

    # -------------- 内部实现 --------------

    def _query_scan(self, query: Query, prune: bool) -> QueryResult:
        rows: List[Dict[str, Any]] = []
        counts: Optional[Dict[Any, int]] = {} if query.count_by is not None else None
        scanned = skipped = 0
        for chunk in self.chunks:
            if prune and not chunk.may_match(query.conds):
                skipped += 1
                continue
            scanned += 1
            for _, rec in chunk.records:
                if record_matches(rec, query.conds):
                    if counts is not None:
                        key = rec.get(query.count_by)
                        counts[key] = counts.get(key, 0) + 1
                    else:
                        rows.append(rec)
        return QueryResult(rows=None if counts is not None else rows, counts=counts,
                           scanned_chunks=scanned, skipped_chunks=skipped)

    def _query_top(self, query: Query, prune: bool, early_stop: bool = True) -> QueryResult:
        n = query.top
        # 按块内最大 ts 倒序处理；ts 相同按块顺序保证确定性
        order = sorted(range(len(self.chunks)),
                       key=lambda i: (self.chunks[i].ts_max is not None,
                                      self.chunks[i].ts_max),
                       reverse=True)
        heap: List[Tuple[Any, int, Dict[str, Any]]] = []  # 最小堆，堆顶为当前第 N 名
        scanned = skipped = 0
        for pos, i in enumerate(order):
            chunk = self.chunks[i]
            if (early_stop and len(heap) >= n
                    and chunk.ts_max is not None
                    and chunk.ts_max < heap[0][0]):
                # 剩余所有块的 ts 上界都严格小于当前第 N 名，不可能再进入前 N
                skipped += len(order) - pos
                break
            if prune and not chunk.may_match(query.conds):
                skipped += 1
                continue
            scanned += 1
            for idx, rec in chunk.records:
                if record_matches(rec, query.conds):
                    key = (rec["ts"], idx)
                    if len(heap) < n:
                        heapq.heappush(heap, (key[0], key[1], rec))
                    elif key > (heap[0][0], heap[0][1]):
                        heapq.heapreplace(heap, (key[0], key[1], rec))
        rows = [rec for _, _, rec in sorted(heap, key=lambda e: (e[0], e[1]), reverse=True)]
        return QueryResult(rows=rows, scanned_chunks=scanned, skipped_chunks=skipped)

    # -------------- 便捷入口 --------------

    def query_jsonable(self, q) -> Dict[str, Any]:
        r = self.query(q)
        out = {
            "scanned_chunks": r.scanned_chunks,
            "skipped_chunks": r.skipped_chunks,
            "elapsed_ms": round(r.elapsed_ms, 3),
        }
        if r.counts is not None:
            out["counts"] = {("null" if k is None else k): v
                             for k, v in sorted(r.counts.items(), key=lambda kv: str(kv[0]))}
        else:
            out["rows"] = r.rows
            out["row_count"] = len(r.rows)
        return out
