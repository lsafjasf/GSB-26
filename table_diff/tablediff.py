"""tablediff: 表级差异对比库（仅标准库）。

按主键对齐比较两张表，输出新增 / 删除 / 修改（字段级）三类差异，
并对主键缺失、主键重复、表结构不一致给出明确报告（绝不按位置静默匹配）。
支持忽略列与数值容差比较，忽略规则会记录在报告中。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

NUMERIC_TYPES = ("int", "float")


def _value_type(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _merge_types(a: str, b: str) -> str:
    if a == b:
        return a
    if a == "none":
        return b
    if b == "none":
        return a
    if a in NUMERIC_TYPES and b in NUMERIC_TYPES:
        return "float"
    return "str"  # 混合类型退化为 str，比较时按 == 处理


@dataclass
class Table:
    """一张待比较的表。

    columns: [(列名, 类型), ...]，类型为 'int'/'float'/'str'/'bool'。
    rows: 可迭代行，每行是与 columns 对齐的 tuple/list。
    """

    name: str
    columns: List[Tuple[str, str]]
    rows: Iterable[Sequence[Any]]

    @classmethod
    def from_dicts(cls, name: str, rows: List[Dict[str, Any]],
                   columns: Optional[List[str]] = None) -> "Table":
        if columns is None:
            columns = []
            seen = set()
            for r in rows:
                for k in r:
                    if k not in seen:
                        seen.add(k)
                        columns.append(k)
        types = {c: "none" for c in columns}
        for r in rows:
            for c in columns:
                types[c] = _merge_types(types[c], _value_type(r.get(c)))
        cols = [(c, t if t != "none" else "str") for c, t in types.items()]
        return cls(name=name, columns=cols,
                   rows=[tuple(r.get(c) for c in columns) for r in rows])


@dataclass
class Tolerance:
    atol: float = 0.0
    rtol: float = 0.0

    def allows(self, a: float, b: float) -> bool:
        return abs(a - b) <= max(self.atol, self.rtol * max(abs(a), abs(b)))


def _parse_tolerances(spec: Optional[Dict[str, Any]]) -> Dict[str, Tolerance]:
    """tolerances: {"*": 0.01} 或 {"col": {"atol":..,"rtol":..}}。"""
    out: Dict[str, Tolerance] = {}
    for col, v in (spec or {}).items():
        if isinstance(v, (int, float)):
            out[col] = Tolerance(atol=float(v))
        elif isinstance(v, dict):
            out[col] = Tolerance(atol=float(v.get("atol", 0.0)),
                                 rtol=float(v.get("rtol", 0.0)))
        else:
            raise ValueError(f"非法容差配置: {col}={v!r}")
    return out


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_equal(a: Any, b: Any, tol: Optional[Tolerance]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if tol is not None and _is_number(a) and _is_number(b):
        return tol.allows(a, b)
    return a == b


@dataclass
class DiffReport:
    summary: Dict[str, Any]
    schema: Dict[str, Any]
    keys: Dict[str, Any]
    rules: Dict[str, Any]
    diff: Dict[str, Any]

    @property
    def equal(self) -> bool:
        return self.summary["equal"]

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary, "schema": self.schema,
                "keys": self.keys, "rules": self.rules, "diff": self.diff}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent,
                          default=repr)

    def to_text(self) -> str:
        s, sch, k, r, d = (self.summary, self.schema, self.keys,
                           self.rules, self.diff)
        lines = [
            "========== 表级差异对比报告 ==========",
            f"左表: {s['left_table']}  右表: {s['right_table']}",
            f"结论: {'一致' if s['equal'] else '不一致'}",
            "",
            "-- 计数与占比 --",
            f"左表行数: {s['rows_left']}   右表行数: {s['rows_right']}",
            f"新增(仅右表): {s['added']}  ({s['added_ratio']:.2%})",
            f"删除(仅左表): {s['deleted']}  ({s['deleted_ratio']:.2%})",
            f"修改(行): {s['modified_rows']}  ({s['modified_row_ratio']:.2%})",
            f"修改(字段): {s['modified_fields']}  ({s['modified_field_ratio']:.2%})",
            f"完全一致行: {s['identical_rows']}",
            f"差异总行数: {s['diff_rows']}  差异占比: {s['diff_row_ratio']:.2%}",
            "",
            "-- 表结构 --",
            f"结构一致: {sch['consistent']}",
            f"仅左表列: {sch['left_only_columns']}",
            f"仅右表列: {sch['right_only_columns']}",
            f"类型不一致列: {sch['type_mismatches']}",
            f"参与比较的列({len(sch['compared_columns'])} 个，不含主键列与类型不一致列): "
            f"{sch['compared_columns']}",
            "",
            "-- 主键健康度 --",
            f"主键: {r['key_columns']}",
            f"左表主键缺失: {k['missing_left_count']}  重复键: {k['duplicates_left_count']}",
            f"右表主键缺失: {k['missing_right_count']}  重复键: {k['duplicates_right_count']}",
            "",
            "-- 比较规则 --",
            f"忽略列: {r['ignored_columns']}",
            f"容差: {r['tolerances']}",
            "",
            f"-- 差异明细(行每类最多 {s['max_examples']} 条, "
            f"每行字段最多 {s['max_field_examples'] if s['max_field_examples'] is not None else '不限'} 条) --",
            f"明细截断: {d['truncated']}  (行数超限: {d['rows_truncated']}, "
            f"字段超限: {d['fields_truncated']})",
            f"新增示例: {json.dumps(d['added'][:3], ensure_ascii=False, default=repr)}",
            f"删除示例: {json.dumps(d['deleted'][:3], ensure_ascii=False, default=repr)}",
            f"修改示例: {json.dumps(d['modified'][:3], ensure_ascii=False, default=repr)}",
        ]
        return "\n".join(lines)


def _normalize_key(key) -> Tuple[str, ...]:
    if isinstance(key, str):
        return (key,)
    return tuple(key)


def _index_rows(rows: Iterable[Sequence[Any]], key_idx: Tuple[int, ...],
                max_examples: int):
    """把行按主键装入字典；缺失/重复主键的行被排除并记录。"""
    keyed: Dict[Tuple[Any, ...], Sequence[Any]] = {}
    missing_examples: List[Dict[str, Any]] = []
    missing_count = 0
    dup_keys: Dict[Tuple[Any, ...], int] = {}
    dup_rows = 0
    for i, row in enumerate(rows):
        k = tuple(row[j] for j in key_idx)
        if any(v is None for v in k):
            missing_count += 1
            if len(missing_examples) < max_examples:
                missing_examples.append({"row_index": i, "key": k})
            continue
        if k in keyed:
            dup_keys[k] = dup_keys.get(k, 1) + 1
            dup_rows += 1
            continue
        keyed[k] = row
    dup_examples = [{"key": k, "occurrences": n}
                    for k, n in list(dup_keys.items())[:max_examples]]
    return keyed, missing_count, missing_examples, dup_keys, dup_rows, dup_examples


def compare(left: Table, right: Table,
            key: Sequence[str] | str,
            ignore_columns: Sequence[str] = (),
            tolerances: Optional[Dict[str, Any]] = None,
            max_examples: int = 1000,
            max_field_examples: Optional[int] = 64) -> DiffReport:
    """比较两张表并返回 DiffReport。

    - key: 主键列（可复合）。
    - ignore_columns: 不参与比较的列。
    - tolerances: 数值容差，{"*": atol} 或 {"col": {"atol":..,"rtol":..}}。
    - max_examples: 报告中每类差异最多保留的明细条数（计数始终精确）。
    - max_field_examples: 每条修改行最多保留的字段级明细条数，None 表示不限；
      超出时该行 fields_truncated 置真、计数仍精确，且报告 diff.truncated 置真。
    """
    key_cols = _normalize_key(key)
    ignore = set(ignore_columns)
    tol_spec = _parse_tolerances(tolerances)
    if not isinstance(max_examples, int) or max_examples < 0:
        raise ValueError(f"max_examples 必须是非负整数: {max_examples!r}")
    if max_field_examples is not None and (
            not isinstance(max_field_examples, int) or max_field_examples < 0):
        raise ValueError(
            f"max_field_examples 必须是非负整数或 None: {max_field_examples!r}")
    field_cap = (float("inf") if max_field_examples is None
                 else max_field_examples)

    # ---------- 表结构对齐（按列名，绝不按位置） ----------
    lcols = dict(left.columns)
    rcols = dict(right.columns)
    left_only = [c for c in lcols if c not in rcols]
    right_only = [c for c in rcols if c not in lcols]
    common = [c for c in lcols if c in rcols]
    type_mismatches = [
        {"column": c, "left_type": lcols[c], "right_type": rcols[c]}
        for c in common
        if lcols[c] != rcols[c]
        and not (lcols[c] in NUMERIC_TYPES and rcols[c] in NUMERIC_TYPES)
    ]
    schema_consistent = not (left_only or right_only or type_mismatches)

    missing_key_cols = [c for c in key_cols if c not in common]
    if missing_key_cols:
        raise ValueError(f"主键列在两侧表中必须同时存在: {missing_key_cols}")

    mismatched_cols = {m["column"] for m in type_mismatches}
    # 参与比较的列：两侧同名同类型（int/float 互容），且既不是主键列也不在忽略列中。
    # 主键列仅用于对齐；类型不一致列只在 schema 段报结构冲突，不做值比较。
    compared = [c for c in common
                if c not in ignore and c not in key_cols and c not in mismatched_cols]
    lidx = {c: i for i, (c, _) in enumerate(left.columns)}
    ridx = {c: i for i, (c, _) in enumerate(right.columns)}
    key_lidx = tuple(lidx[c] for c in key_cols)
    key_ridx = tuple(ridx[c] for c in key_cols)

    # ---------- 建键索引，收集缺失/重复主键 ----------
    (lkeyed, lmiss_n, lmiss_ex, ldup_keys, ldup_rows, ldup_ex) = \
        _index_rows(left.rows, key_lidx, max_examples)
    (rkeyed, rmiss_n, rmiss_ex, rdup_keys, rdup_rows, rdup_ex) = \
        _index_rows(right.rows, key_ridx, max_examples)

    # ---------- 对齐比较 ----------
    def tol_for(col: str) -> Optional[Tolerance]:
        return tol_spec.get(col, tol_spec.get("*"))

    added: List[Dict[str, Any]] = []
    deleted: List[Dict[str, Any]] = []
    modified: List[Dict[str, Any]] = []
    n_added = n_deleted = n_modified_rows = n_modified_fields = 0
    identical = 0
    n_field_truncated_rows = 0

    field_idx = [(c, lidx[c], ridx[c]) for c in compared]

    for k, lrow in lkeyed.items():
        rrow = rkeyed.get(k)
        if rrow is None:
            n_deleted += 1
            if len(deleted) < max_examples:
                deleted.append({"key": k, "row": list(lrow)})
            continue
        fields = []
        nfields = 0
        fields_truncated = False
        for c, li, ri in field_idx:
            lv, rv = lrow[li], rrow[ri]
            if not values_equal(lv, rv, tol_for(c)):
                nfields += 1
                if len(fields) < field_cap:
                    fields.append({"column": c, "left": lv, "right": rv})
                else:
                    fields_truncated = True
        if nfields:
            n_modified_rows += 1
            n_modified_fields += nfields
            if fields_truncated:
                n_field_truncated_rows += 1
            if len(modified) < max_examples:
                modified.append({"key": k, "fields": fields,
                                 "field_diff_count": nfields,
                                 "fields_truncated": fields_truncated})
        else:
            identical += 1

    for k, rrow in rkeyed.items():
        if k not in lkeyed:
            n_added += 1
            if len(added) < max_examples:
                added.append({"key": k, "row": list(rrow)})

    # ---------- 汇总 ----------
    rows_left = len(lkeyed) + lmiss_n + ldup_rows
    rows_right = len(rkeyed) + rmiss_n + rdup_rows
    base = max(rows_left, rows_right, 1)
    common_rows = len(lkeyed) - n_deleted
    diff_rows = n_added + n_deleted + n_modified_rows
    total_fields = max(common_rows * max(len(compared), 1), 1)

    summary = {
        "left_table": left.name,
        "right_table": right.name,
        "equal": (n_added == 0 and n_deleted == 0 and n_modified_rows == 0
                  and schema_consistent and lmiss_n == 0 and rmiss_n == 0
                  and not ldup_keys and not rdup_keys),
        "rows_left": rows_left,
        "rows_right": rows_right,
        "added": n_added,
        "added_ratio": n_added / base,
        "deleted": n_deleted,
        "deleted_ratio": n_deleted / base,
        "modified_rows": n_modified_rows,
        "modified_row_ratio": n_modified_rows / base,
        "modified_fields": n_modified_fields,
        "modified_field_ratio": n_modified_fields / total_fields,
        "identical_rows": identical,
        "diff_rows": diff_rows,
        "diff_row_ratio": diff_rows / base,
        "max_examples": max_examples,
        "max_field_examples": max_field_examples,
    }
    schema = {
        "consistent": schema_consistent,
        "left_only_columns": left_only,
        "right_only_columns": right_only,
        "type_mismatches": type_mismatches,
        "compared_columns": compared,
        "ignored_columns": sorted(ignore),
    }
    keys = {
        "missing_left_count": lmiss_n,
        "missing_left_examples": lmiss_ex,
        "missing_right_count": rmiss_n,
        "missing_right_examples": rmiss_ex,
        "duplicates_left_count": len(ldup_keys),
        "duplicates_left_rows_excluded": ldup_rows,
        "duplicates_left_examples": ldup_ex,
        "duplicates_right_count": len(rdup_keys),
        "duplicates_right_rows_excluded": rdup_rows,
        "duplicates_right_examples": rdup_ex,
    }
    rules = {
        "key_columns": list(key_cols),
        "ignored_columns": sorted(ignore),
        "max_examples": max_examples,
        "max_field_examples": max_field_examples,
        "tolerances": {c: {"atol": t.atol, "rtol": t.rtol}
                       for c, t in sorted(tol_spec.items())},
    }
    rows_truncated = any(n > max_examples
                         for n in (n_added, n_deleted, n_modified_rows))
    fields_truncated = n_field_truncated_rows > 0
    diff = {"added": added, "deleted": deleted, "modified": modified,
            "rows_truncated": rows_truncated,
            "fields_truncated": fields_truncated,
            "field_truncated_rows": n_field_truncated_rows,
            "truncated": rows_truncated or fields_truncated}
    return DiffReport(summary=summary, schema=schema, keys=keys,
                      rules=rules, diff=diff)
