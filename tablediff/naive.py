"""朴素全量比较实现（O(n²)），仅用于对拍验证，不参与生产路径。

与 core.diff_tables 完全独立：不建索引，逐行线性扫描找相同主键。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Set, Tuple

from .core import DiffConfig, Table


def _eq(a: Any, b: Any, abs_tol: float, rel_tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b and type(a) is type(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)
    return a == b


def naive_diff(left: Table, right: Table, cfg: DiffConfig) -> Dict[str, Any]:
    """返回与 DiffReport 对应的计数/集合字典，供对拍断言。"""
    left_cols = {c.name: c for c in left.schema}
    right_cols = {c.name: c for c in right.schema}
    ignored = set(cfg.ignore_columns)
    mismatch = {
        i
        for i in left_cols.keys() & right_cols.keys()
        if left_cols[i].type != right_cols[i].type
        and not ({left_cols[i].type, right_cols[i].type} <= {"int", "float"})
    }
    key_set = set(cfg.key_columns)
    compared = sorted(
        c
        for c in left_cols.keys() & right_cols.keys()
        if c not in ignored and c not in mismatch and c not in key_set
    )
    lidx = {c: left.col_index(c) for c in compared}
    ridx = {c: right.col_index(c) for c in compared}
    lk = [left.col_index(k) for k in cfg.key_columns]
    rk = [right.col_index(k) for k in cfg.key_columns]

    def key_of(row, idxs):
        return tuple(row[i] for i in idxs)

    # 朴素地统计缺失/重复键（线性扫描计数）
    def key_stats(table, idxs):
        keys = []
        missing = 0
        for row in table.rows:
            k = key_of(row, idxs)
            if any(v is None for v in k):
                missing += 1
            else:
                keys.append(k)
        dup_rows = 0
        for i, k in enumerate(keys):
            if keys.count(k) > 1:
                dup_rows += 1
        return keys, dup_rows, missing

    lkeys, l_dup, l_miss = key_stats(left, lk)
    rkeys, r_dup, r_miss = key_stats(right, rk)
    l_unique = [k for k in lkeys if lkeys.count(k) == 1]
    r_unique = [k for k in rkeys if rkeys.count(k) == 1]

    def find(row_key, rows, idxs):
        for row in rows:
            k = key_of(row, idxs)
            if any(v is None for v in k):
                continue
            if k == row_key:
                return row
        return None

    added: Set[Tuple] = set()
    removed: Set[Tuple] = set()
    modified: Set[Tuple] = set()
    field_diffs: Set[Tuple] = set()

    for lrow in left.rows:
        k = key_of(lrow, lk)
        if any(v is None for v in k) or k not in l_unique:
            continue
        rrow = find(k, right.rows, rk)
        if rrow is None or k not in r_unique:
            removed.add(k)
            continue
        for c in compared:
            at, rt = cfg.tol_for(c)
            if not _eq(lrow[lidx[c]], rrow[ridx[c]], at, rt):
                modified.add(k)
                field_diffs.add((k, c))

    for rrow in right.rows:
        k = key_of(rrow, rk)
        if any(v is None for v in k) or k not in r_unique:
            continue
        if find(k, left.rows, lk) is None or k not in l_unique:
            added.add(k)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "field_diffs": field_diffs,
        "left_dup": l_dup,
        "right_dup": r_dup,
        "left_missing": l_miss,
        "right_missing": r_miss,
        "compared_columns": compared,
    }
