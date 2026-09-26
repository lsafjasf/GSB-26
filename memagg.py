"""memagg: memory-bounded streaming group-by aggregation and 2D pivot/cross-tab.

Only the Python standard library is used.

How it works (two phases):
  Phase 1 (partial aggregation + spill): records are streamed into an in-memory
  dict of per-group partial states. When the estimated memory footprint exceeds
  the configured budget, the dict is sorted by group key and flushed to disk as
  a "run" file, then aggregation continues with an empty dict.
  Phase 2 (merge): all sorted runs plus the final in-memory dict are k-way
  merged (heapq.merge) and partial states for equal keys are combined, yielding
  final results in sorted-key order. Peak memory stays bounded by the budget
  plus one group's state per merge step.

Null / missing-value rules (see README.md):
  * Group key None or float NaN  -> normalized to the single missing-key group,
    reported as None in the output.
  * bool keys are normalized to int (True -> 1) so they cannot collide with 0/1.
  * Value None or NaN -> ignored by sum/min/max/count_distinct; "count" always
    counts records (including records whose value is missing).
  * A group whose values are all missing gets sum/min/max = None,
    count_distinct = 0, count = number of records.
  * Duplicate records are NOT deduplicated; every record contributes.

Exactness note: integer sums are exact. Float sums use plain addition; for
pathological float data the result can differ from a naive in-memory sum in the
last ulp because partial sums are combined in a different order.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import shutil
import sys
import tempfile

AGGS = ("sum", "count", "min", "max", "count_distinct")

# Rough per-entry memory estimates (bytes) used for the budget check. The
# budget is a heuristic guard, not a hard RSS limit.
_GROUP_BASE_OVERHEAD = 168        # dict slot + state list + scalar states
_GROUP_DISTINCT_OVERHEAD = 232    # one (initially empty) set per group
_DISTINCT_ELEM_OVERHEAD = 56

_UNSET = object()


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _norm_key(k):
    if _is_missing(k):
        return None
    if isinstance(k, bool):
        return int(k)
    return k


def _sort_key(k):
    """Total, deterministic ordering across mixed key types."""
    if k is None:
        return (0, 0)
    if isinstance(k, (int, float)):
        return (1, float(k))
    if isinstance(k, str):
        return (2, k)
    if isinstance(k, tuple):
        return (3, tuple(_sort_key(x) for x in k))
    return (4, repr(k))


def _decode(obj):
    """JSON turns tuples into lists; restore them so keys stay hashable."""
    if isinstance(obj, list):
        return tuple(_decode(x) for x in obj)
    return obj


class GroupBy:
    """Streaming group-by aggregation with a configurable memory budget.

    Usage:
        gb = GroupBy(["sum", "count", "min", "max", "count_distinct"],
                     memory_budget=8 << 20)
        for key, value in records:
            gb.add(key, value)
        for key, aggs in gb.results():   # sorted by group key
            ...
    """

    def __init__(self, aggs, memory_budget=64 << 20, spill_dir=None):
        aggs = tuple(aggs)
        bad = [a for a in aggs if a not in AGGS]
        if bad:
            raise ValueError(f"unknown aggregations: {bad}; supported: {AGGS}")
        if not aggs:
            raise ValueError("at least one aggregation is required")
        if memory_budget <= 0:
            raise ValueError("memory_budget must be positive")
        self._aggs = aggs
        self._budget = memory_budget
        self._groups = {}          # key -> list of partial states (aligned with aggs)
        self._est = 0              # estimated bytes held by _groups
        self._runs = []            # spill file paths
        self._records = 0
        self._finalized = False
        self._dir = tempfile.mkdtemp(prefix="memagg-", dir=spill_dir)
        self._group_overhead = _GROUP_BASE_OVERHEAD + (
            _GROUP_DISTINCT_OVERHEAD if "count_distinct" in aggs else 0)

    # ------------------------------------------------------------------ input

    def add(self, key, value=None):
        if self._finalized:
            raise RuntimeError("cannot add after results()")
        key = _norm_key(key)
        st = self._groups.get(key)
        if st is None:
            st = self._new_state()
            self._groups[key] = st
            self._est += self._group_overhead + sys.getsizeof(key)
        missing = _is_missing(value)
        for i, agg in enumerate(self._aggs):
            if agg == "count":
                st[i] += 1
            elif missing:
                continue
            elif agg == "sum":
                st[i] = value if st[i] is None else st[i] + value
            elif agg == "min":
                if st[i] is None or value < st[i]:
                    st[i] = value
            elif agg == "max":
                if st[i] is None or value > st[i]:
                    st[i] = value
            elif agg == "count_distinct":
                s = st[i]
                if value not in s:
                    s.add(value)
                    self._est += sys.getsizeof(value) + _DISTINCT_ELEM_OVERHEAD
        self._records += 1
        if self._est >= self._budget:
            self._spill()

    def update(self, records):
        for key, value in records:
            self.add(key, value)

    # ----------------------------------------------------------------- output

    def results(self):
        """Yield (key, {agg: value}) once, in sorted-key order, then clean up."""
        if self._finalized:
            raise RuntimeError("results() may only be consumed once")
        self._finalized = True
        files = []
        try:
            iters = []
            for path in self._runs:
                f = open(path, "r", encoding="utf-8")
                files.append(f)
                iters.append(self._iter_run(f))
            iters.append(self._iter_memory())
            merged = heapq.merge(*iters, key=lambda item: item[0])
            cur_key = _UNSET
            cur_state = None
            for _, key, state in merged:
                if cur_state is not None and key == cur_key:
                    cur_state = self._combine(cur_state, state)
                else:
                    if cur_state is not None:
                        yield cur_key, self._finalize(cur_state)
                    cur_key, cur_state = key, state
            if cur_state is not None:
                yield cur_key, self._finalize(cur_state)
        finally:
            for f in files:
                f.close()
            self._cleanup()

    def to_dict(self):
        return {key: aggs for key, aggs in self.results()}

    # --------------------------------------------------------------- internals

    @property
    def spill_count(self):
        return len(self._runs)

    @property
    def record_count(self):
        return self._records

    def _new_state(self):
        st = []
        for agg in self._aggs:
            if agg == "count":
                st.append(0)
            elif agg == "count_distinct":
                st.append(set())
            else:
                st.append(None)
        return st

    def _combine(self, a, b):
        out = []
        for i, agg in enumerate(self._aggs):
            x, y = a[i], b[i]
            if agg == "count":
                out.append(x + y)
            elif agg == "count_distinct":
                out.append(x | y)
            elif agg == "sum":
                out.append(x if y is None else y if x is None else x + y)
            elif agg == "min":
                out.append(x if y is None else y if x is None else min(x, y))
            elif agg == "max":
                out.append(x if y is None else y if x is None else max(x, y))
        return out

    def _finalize(self, st):
        out = {}
        for i, agg in enumerate(self._aggs):
            out[agg] = len(st[i]) if agg == "count_distinct" else st[i]
        return out

    def _encode_state(self, st):
        out = []
        for i, agg in enumerate(self._aggs):
            if agg == "count_distinct":
                out.append(sorted(st[i], key=_sort_key))
            else:
                out.append(st[i])
        return out

    def _decode_state(self, raw):
        out = []
        for i, agg in enumerate(self._aggs):
            if agg == "count_distinct":
                out.append(set(_decode(v) for v in raw[i]))
            else:
                out.append(_decode(raw[i]))
        return out

    def _spill(self):
        if not self._groups:
            return
        path = os.path.join(self._dir, f"run-{len(self._runs):05d}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for key, st in sorted(self._groups.items(), key=lambda kv: _sort_key(kv[0])):
                f.write(json.dumps([key, self._encode_state(st)]) + "\n")
        self._runs.append(path)
        self._groups = {}
        self._est = 0

    def _iter_run(self, f):
        for line in f:
            key, raw = json.loads(line)
            key = _decode(key)
            yield (_sort_key(key), key, self._decode_state(raw))

    def _iter_memory(self):
        for key, st in sorted(self._groups.items(), key=lambda kv: _sort_key(kv[0])):
            yield (_sort_key(key), key, st)

    def _cleanup(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def close(self):
        self._cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cleanup()
        return False


class Pivot:
    """Memory-bounded 2D cross-tabulation built on GroupBy.

    pv = Pivot("sum", memory_budget=...)
    pv.add(row_key, col_key, value)
    table = pv.result()  # {"rows": [...], "cols": [...], "cells": {(r, c): val}}
    Missing row/col keys are normalized to None, same rules as GroupBy.
    """

    def __init__(self, agg="sum", memory_budget=64 << 20, spill_dir=None):
        if agg not in AGGS:
            raise ValueError(f"unknown aggregation {agg!r}; supported: {AGGS}")
        self.agg = agg
        self._gb = GroupBy([agg], memory_budget=memory_budget, spill_dir=spill_dir)

    def add(self, row, col, value=None):
        self._gb.add((_norm_key(row), _norm_key(col)), value)

    def update(self, records):
        for row, col, value in records:
            self.add(row, col, value)

    @property
    def spill_count(self):
        return self._gb.spill_count

    def result(self):
        rows, cols, cells = set(), set(), {}
        for key, aggs in self._gb.results():
            r, c = key
            rows.add(r)
            cols.add(c)
            cells[(r, c)] = aggs[self.agg]
        return {
            "rows": sorted(rows, key=_sort_key),
            "cols": sorted(cols, key=_sort_key),
            "cells": cells,
        }
