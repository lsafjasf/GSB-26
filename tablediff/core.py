"""核心 diff 引擎。

设计要点：
- 行按主键（支持复合主键）对齐，绝不按位置静默匹配。
- 主键为 None（缺失）或重复的行不参与对齐，单独计数并抽样记录。
- 列按列名匹配；仅一侧存在的列、同名但类型不同的列都会记入报告，
  类型不一致的列不参与值比较。
- 数值列可配置绝对/相对容差；忽略列与容差规则原样记录进报告。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

NUMERIC_TYPES = {"int", "float"}
KNOWN_TYPES = {"int", "float", "str", "bool", "none"}


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # 'int' | 'float' | 'str' | 'bool' | 'none'

    def __post_init__(self):
        if self.type not in KNOWN_TYPES:
            raise ValueError(f"unknown column type: {self.type!r}")


class Table:
    """一张表：schema + 行（tuple 序列，顺序与 schema 一致）。"""

    def __init__(self, schema: Sequence[Column], rows: Iterable[Sequence[Any]]):
        self.schema: List[Column] = list(schema)
        names = [c.name for c in self.schema]
        if len(names) != len(set(names)):
            raise ValueError("schema contains duplicate column names")
        self.rows: List[Tuple[Any, ...]] = [tuple(r) for r in rows]
        for i, r in enumerate(self.rows):
            if len(r) != len(self.schema):
                raise ValueError(
                    f"row {i} has {len(r)} values, schema has {len(self.schema)} columns"
                )
        self._col_index: Dict[str, int] = {c.name: i for i, c in enumerate(self.schema)}

    def col_index(self, name: str) -> int:
        return self._col_index[name]

    def column(self, name: str) -> Optional[Column]:
        i = self._col_index.get(name)
        return self.schema[i] if i is not None else None

    def __len__(self) -> int:
        return len(self.rows)


def _infer_type(values: Iterable[Any]) -> str:
    seen = set()
    for v in values:
        if v is None:
            seen.add("none")
        elif isinstance(v, bool):
            seen.add("bool")
        elif isinstance(v, int):
            seen.add("int")
        elif isinstance(v, float):
            seen.add("float")
        else:
            seen.add("str")
    if not seen:
        return "none"
    if seen <= {"none"}:
        return "none"
    seen.discard("none")
    if seen == {"int"}:
        return "int"
    if seen <= {"int", "float"}:
        return "float"
    if len(seen) == 1:
        return seen.pop()
    return "str"  # 混合类型按 str 处理


def infer_schema(names: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[Column]:
    """从数据推断 schema（供测试/演示便捷使用）。"""
    cols = []
    for j, name in enumerate(names):
        cols.append(Column(name, _infer_type(r[j] for r in rows)))
    return cols


@dataclass
class DiffConfig:
    """比较配置。"""

    key_columns: Tuple[str, ...] = ("id",)
    ignore_columns: Tuple[str, ...] = ()
    # 每列容差：{列名: (abs_tol, rel_tol)}；default_*_tol 作用于未单独配置的数值列
    tolerances: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    default_abs_tol: float = 0.0
    default_rel_tol: float = 0.0
    max_samples: int = 10  # 报告中每类异常/差异的抽样条数
    max_field_diffs: int = 100000  # 字段级差异明细的最大记录条数（计数不受限）

    def tol_for(self, column: str) -> Tuple[float, float]:
        return self.tolerances.get(column, (self.default_abs_tol, self.default_rel_tol))


@dataclass
class SchemaIssue:
    kind: str  # 'missing_in_left' | 'missing_in_right' | 'type_mismatch'
    column: str
    left_type: Optional[str] = None
    right_type: Optional[str] = None


@dataclass
class FieldDiff:
    key: Tuple[Any, ...]
    column: str
    left: Any
    right: Any


@dataclass
class DiffReport:
    """比较结果。计数是精确的；明细列表按 config 上限截断。"""

    left_rows: int = 0
    right_rows: int = 0
    schema_issues: List[SchemaIssue] = field(default_factory=list)
    compared_columns: List[str] = field(default_factory=list)
    ignored_columns: List[str] = field(default_factory=list)
    tolerance_rules: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    added_count: int = 0
    removed_count: int = 0
    modified_count: int = 0  # 有字段差异的行数
    field_diff_count: int = 0  # 字段级差异总数

    added_keys: List[Tuple[Any, ...]] = field(default_factory=list)
    removed_keys: List[Tuple[Any, ...]] = field(default_factory=list)
    modified_keys: List[Tuple[Any, ...]] = field(default_factory=list)
    field_diffs: List[FieldDiff] = field(default_factory=list)
    field_diffs_truncated: bool = False

    left_duplicate_keys: int = 0
    right_duplicate_keys: int = 0
    left_missing_keys: int = 0
    right_missing_keys: int = 0
    left_duplicate_samples: List[Tuple[Any, ...]] = field(default_factory=list)
    right_duplicate_samples: List[Tuple[Any, ...]] = field(default_factory=list)

    @property
    def union_keys(self) -> int:
        return self.added_count + self.removed_count + self.common_keys

    common_keys: int = 0

    @property
    def total_diff_rows(self) -> int:
        return self.added_count + self.removed_count + self.modified_count

    @property
    def diff_ratio(self) -> float:
        base = self.union_keys
        return self.total_diff_rows / base if base else 0.0

    @property
    def identical(self) -> bool:
        return (
            self.total_diff_rows == 0
            and not self.schema_issues
            and self.left_duplicate_keys == 0
            and self.right_duplicate_keys == 0
            and self.left_missing_keys == 0
            and self.right_missing_keys == 0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_rows": self.left_rows,
            "right_rows": self.right_rows,
            "schema_issues": [vars(i) for i in self.schema_issues],
            "compared_columns": self.compared_columns,
            "ignored_columns": self.ignored_columns,
            "tolerance_rules": {k: list(v) for k, v in self.tolerance_rules.items()},
            "counts": {
                "added": self.added_count,
                "removed": self.removed_count,
                "modified_rows": self.modified_count,
                "field_diffs": self.field_diff_count,
                "total_diff_rows": self.total_diff_rows,
                "union_keys": self.union_keys,
                "diff_ratio": self.diff_ratio,
            },
            "key_anomalies": {
                "left_duplicate_keys": self.left_duplicate_keys,
                "right_duplicate_keys": self.right_duplicate_keys,
                "left_missing_keys": self.left_missing_keys,
                "right_missing_keys": self.right_missing_keys,
            },
        }

    def to_text(self) -> str:
        lines = []
        a = lines.append
        a("=" * 60)
        a("表级差异对比报告")
        a("=" * 60)
        a(f"左表行数: {self.left_rows}    右表行数: {self.right_rows}")
        a(f"参与比较的列 ({len(self.compared_columns)}): {', '.join(self.compared_columns) or '(无)'}")
        if self.ignored_columns:
            a(f"忽略的列: {', '.join(self.ignored_columns)}")
        if self.tolerance_rules:
            a("容差规则 (abs_tol, rel_tol):")
            for col, (at, rt) in sorted(self.tolerance_rules.items()):
                a(f"  - {col}: abs={at}, rel={rt}")
        a("")
        if self.schema_issues:
            a(f"[表结构不一致] 共 {len(self.schema_issues)} 项:")
            for i in self.schema_issues:
                if i.kind == "missing_in_left":
                    a(f"  - 列 {i.column!r} 仅存在于右表 (right: {i.right_type})")
                elif i.kind == "missing_in_right":
                    a(f"  - 列 {i.column!r} 仅存在于左表 (left: {i.left_type})")
                else:
                    a(f"  - 列 {i.column!r} 类型不一致: left={i.left_type} right={i.right_type}（该列不参与比较）")
            a("")
        anomalies = [
            ("左表主键重复", self.left_duplicate_keys, self.left_duplicate_samples),
            ("右表主键重复", self.right_duplicate_keys, self.right_duplicate_samples),
            ("左表主键缺失", self.left_missing_keys, None),
            ("右表主键缺失", self.right_missing_keys, None),
        ]
        if any(c for _, c, _ in anomalies):
            a("[主键异常]（这些行不参与对齐比较）:")
            for label, count, samples in anomalies:
                if count:
                    a(f"  - {label}: {count} 行")
                    if samples:
                        a(f"    抽样: {samples}")
            a("")
        a("[差异统计]")
        a(f"  新增 (仅右表): {self.added_count}")
        a(f"  删除 (仅左表): {self.removed_count}")
        a(f"  修改 (行级):   {self.modified_count}  (字段级差异 {self.field_diff_count} 处)")
        a(f"  公共主键:      {self.common_keys}")
        a(f"  差异合计:      {self.total_diff_rows} / {self.union_keys} "
          f"(占比 {self.diff_ratio:.2%})")
        a("")
        if self.added_keys:
            a(f"新增主键抽样: {self.added_keys}")
        if self.removed_keys:
            a(f"删除主键抽样: {self.removed_keys}")
        if self.field_diffs:
            a(f"字段级差异明细 (前 {len(self.field_diffs)} 条"
              f"{'，已截断' if self.field_diffs_truncated else ''}):")
            for fd in self.field_diffs[:50]:
                a(f"  key={fd.key} 列={fd.column!r}: {fd.left!r} -> {fd.right!r}")
        a("")
        a(f"结论: {'两表一致' if self.identical else '两表存在差异'}")
        return "\n".join(lines)


def _values_equal(a: Any, b: Any, abs_tol: float, rel_tol: float) -> bool:
    if a is b:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if a == b:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs_tol == 0.0 and rel_tol == 0.0:
            return False  # a == b 已失败
        return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)
    return False


def _build_key_index(
    table: Table, key_idx: Sequence[int]
) -> Tuple[Dict[Tuple[Any, ...], Tuple[Any, ...]], int, int, List[Tuple[Any, ...]]]:
    """返回 (唯一键 -> 行, 重复键行数, 缺失键行数, 重复键抽样)。"""
    seen: Dict[Tuple[Any, ...], Tuple[Any, ...]] = {}
    dup_keys: set = set()
    dup_count = 0
    missing = 0
    for row in table.rows:
        key = tuple(row[i] for i in key_idx)
        if any(v is None for v in key):
            missing += 1
            continue
        if key in seen or key in dup_keys:
            dup_keys.add(key)
            dup_count += 1
            continue
        seen[key] = row
    # 首次出现后被再次撞上的 key，其首行也视为重复（不参与对齐）
    for key in dup_keys:
        if key in seen:
            del seen[key]
            dup_count += 1
    samples = sorted(dup_keys, key=repr)[:10]
    return seen, dup_count, missing, samples


def diff_tables(left: Table, right: Table, config: Optional[DiffConfig] = None) -> DiffReport:
    cfg = config or DiffConfig()
    rep = DiffReport(left_rows=len(left), right_rows=len(right))

    # ---- 1. 结构对比（按列名，不按位置）----
    left_cols = {c.name: c for c in left.schema}
    right_cols = {c.name: c for c in right.schema}
    for name in left_cols:
        if name not in right_cols:
            rep.schema_issues.append(
                SchemaIssue("missing_in_right", name, left_type=left_cols[name].type)
            )
    for name in right_cols:
        if name not in left_cols:
            rep.schema_issues.append(
                SchemaIssue("missing_in_left", name, right_type=right_cols[name].type)
            )
    for name in left_cols.keys() & right_cols.keys():
        lt, rt = left_cols[name].type, right_cols[name].type
        if lt != rt and not ({lt, rt} <= NUMERIC_TYPES):
            rep.schema_issues.append(
                SchemaIssue("type_mismatch", name, left_type=lt, right_type=rt)
            )

    # ---- 2. 主键列校验 ----
    for kc in cfg.key_columns:
        if kc not in left_cols or kc not in right_cols:
            raise ValueError(f"key column {kc!r} must exist in both tables")

    ignored = set(cfg.ignore_columns)
    rep.ignored_columns = sorted(ignored)
    key_set = set(cfg.key_columns)
    compared = [
        name
        for name in left_cols.keys() & right_cols.keys()
        if name not in ignored
        and name not in key_set  # 主键列用于对齐，不作为值列比较
        and not any(i.kind == "type_mismatch" and i.column == name for i in rep.schema_issues)
    ]
    rep.compared_columns = sorted(compared)
    rep.tolerance_rules = {
        c: cfg.tol_for(c)
        for c in compared
        if left_cols[c].type in NUMERIC_TYPES
        and cfg.tol_for(c) != (0.0, 0.0)
    }

    # ---- 3. 主键索引 ----
    lk_idx = [left.col_index(k) for k in cfg.key_columns]
    rk_idx = [right.col_index(k) for k in cfg.key_columns]
    left_map, rep.left_duplicate_keys, rep.left_missing_keys, rep.left_duplicate_samples = (
        _build_key_index(left, lk_idx)
    )
    right_map, rep.right_duplicate_keys, rep.right_missing_keys, rep.right_duplicate_samples = (
        _build_key_index(right, rk_idx)
    )

    # ---- 4. 对齐比较 ----
    lcol_idx = {c: left.col_index(c) for c in compared}
    rcol_idx = {c: right.col_index(c) for c in compared}
    tols = {c: cfg.tol_for(c) for c in compared}

    added_keys: List[Tuple[Any, ...]] = []
    removed_keys: List[Tuple[Any, ...]] = []
    modified_keys: List[Tuple[Any, ...]] = []
    field_diffs: List[FieldDiff] = []

    for key, lrow in left_map.items():
        rrow = right_map.get(key)
        if rrow is None:
            rep.removed_count += 1
            if len(removed_keys) < cfg.max_samples:
                removed_keys.append(key)
            continue
        rep.common_keys += 1
        row_diffs = 0
        for col in compared:
            lv, rv = lrow[lcol_idx[col]], rrow[rcol_idx[col]]
            at, rt = tols[col]
            if not _values_equal(lv, rv, at, rt):
                row_diffs += 1
                rep.field_diff_count += 1
                if len(field_diffs) < cfg.max_field_diffs:
                    field_diffs.append(FieldDiff(key, col, lv, rv))
                else:
                    rep.field_diffs_truncated = True
        if row_diffs:
            rep.modified_count += 1
            if len(modified_keys) < cfg.max_samples:
                modified_keys.append(key)

    for key in right_map:
        if key not in left_map:
            rep.added_count += 1
            if len(added_keys) < cfg.max_samples:
                added_keys.append(key)

    rep.added_keys = added_keys
    rep.removed_keys = removed_keys
    rep.modified_keys = modified_keys
    rep.field_diffs = field_diffs
    return rep
