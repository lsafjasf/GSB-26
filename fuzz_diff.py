#!/usr/bin/env python3
"""对拍脚本：随机生成数据集，比较 diff_tables 与 naive_diff 的结果。

用法: python3 fuzz_diff.py [轮数] [种子]
"""

import random
import sys

from tablediff import Column, DiffConfig, Table, diff_tables
from tablediff.naive import naive_diff

TYPES = ["int", "float", "str", "bool"]


def rand_value(rng, typ):
    if typ == "int":
        return rng.randint(-10**6, 10**6)
    if typ == "float":
        return round(rng.uniform(-1000, 1000), rng.randint(0, 6))
    if typ == "bool":
        return rng.choice([True, False])
    return "".join(rng.choices("abcdefg0123", k=rng.randint(0, 8)))


def gen_case(rng):
    """生成一对表 + 配置，覆盖各种异常情形。"""
    ncols = rng.randint(2, 8)
    col_names = [f"c{i}" for i in range(ncols)]
    types = [rng.choice(TYPES) for _ in range(ncols)]
    schema = [Column(n, t) for n, t in zip(col_names, types)]
    key_columns = ("c0",)

    n = rng.randint(0, 300)
    base_rows = []
    for i in range(n):
        row = [rand_value(rng, t) for t in types]
        row[0] = i if types[0] == "int" else f"k{i}"
        base_rows.append(tuple(row))

    # 主键类型固定为可保证唯一的类型
    key_type = "int" if types[0] == "int" else "str"
    schema[0] = Column("c0", key_type)
    base_rows = [
        (r[0] if isinstance(r[0], (int, str)) else (len(base_rows) if key_type == "int" else "k"),) + r[1:]
        for r in base_rows
    ]
    base_rows = [
        (i if key_type == "int" else f"k{i}",) + r[1:] for i, r in enumerate(base_rows)
    ]

    left_rows = list(base_rows)
    right_rows = list(base_rows)

    # 随机扰动右表：删、增、改、乱序
    for r in right_rows:
        pass
    right_rows = [r for r in right_rows if rng.random() > 0.05]  # 删除
    next_id = n
    for _ in range(rng.randint(0, max(1, n // 10))):  # 新增
        row = [rand_value(rng, t) for t in types]
        row[0] = next_id if key_type == "int" else f"k{next_id}"
        next_id += 1
        right_rows.append(tuple(row))
    for i in range(len(right_rows)):  # 修改
        if rng.random() < 0.1:
            row = list(right_rows[i])
            j = rng.randrange(1, ncols)
            if types[j] == "float" and rng.random() < 0.5:
                # 容差内/外的微小扰动
                row[j] = (row[j] or 0.0) + rng.choice([1e-9, 0.5, -0.5])
            else:
                row[j] = rand_value(rng, types[j])
            right_rows[i] = tuple(row)
    rng.shuffle(right_rows)
    rng.shuffle(left_rows)

    # 注入主键异常
    for rows in (left_rows, right_rows):
        if rows and rng.random() < 0.3:  # 重复主键
            rows.append(rows[rng.randrange(len(rows))])
        if rows and rng.random() < 0.3:  # 缺失主键
            row = list(rows[rng.randrange(len(rows))])
            row[0] = None
            rows.append(tuple(row))

    # 结构扰动：仅右表多列 / 仅左表多列 / 类型不一致
    left_schema, right_schema = list(schema), list(schema)
    left_extra, right_extra = [], []
    if rng.random() < 0.3:
        right_schema.append(Column("extra_r", "str"))
        right_rows = [r + ("x",) for r in right_rows]
    if rng.random() < 0.3:
        left_schema.append(Column("extra_l", "int"))
        left_rows = [r + (1,) for r in left_rows]
    if rng.random() < 0.2 and ncols > 1:
        # 左表把某列类型改成 str（类型不一致）
        j = rng.randrange(1, ncols)
        left_schema[j] = Column(col_names[j], "str")
        left_rows = [
            r[:j] + (str(r[j]),) + r[j + 1:] for r in left_rows
        ]

    ignore = tuple(c for c in col_names[1:] if rng.random() < 0.2)
    tolerances = {}
    for name, t in zip(col_names, types):
        if t == "float" and rng.random() < 0.5:
            tolerances[name] = (rng.choice([1e-6, 1e-3, 0.6]), 0.0)

    cfg = DiffConfig(
        key_columns=key_columns,
        ignore_columns=ignore,
        tolerances=tolerances,
        max_samples=5,
        max_field_diffs=10**9,
    )
    left = Table(left_schema, left_rows)
    right = Table(right_schema, right_rows)
    return left, right, cfg


def run_rounds(rounds, seed):
    rng = random.Random(seed)
    for it in range(rounds):
        left, right, cfg = gen_case(rng)
        rep = diff_tables(left, right, cfg)
        ref = naive_diff(left, right, cfg)

        got_added = set()
        got_removed = set()
        got_modified = set()
        got_field = set()
        # 从完整明细重建集合（max_field_diffs 足够大，未截断）
        assert not rep.field_diffs_truncated
        # 重新计算完整集合：report 只存抽样 key，但计数精确；
        # 这里通过 field_diffs 明细 + added/removed 计数与朴素结果对拍。
        for fd in rep.field_diffs:
            got_field.add((fd.key, fd.column))
            got_modified.add(fd.key)

        # added/removed 的完整集合通过对拍计数 + 抽样隶属校验
        assert rep.added_count == len(ref["added"]), (
            f"iter {it}: added {rep.added_count} != {len(ref['added'])}")
        assert rep.removed_count == len(ref["removed"]), (
            f"iter {it}: removed {rep.removed_count} != {len(ref['removed'])}")
        assert rep.modified_count == len(ref["modified"]), (
            f"iter {it}: modified {rep.modified_count} != {len(ref['modified'])}")
        assert rep.field_diff_count == len(ref["field_diffs"]), (
            f"iter {it}: field_diffs {rep.field_diff_count} != {len(ref['field_diffs'])}")
        assert got_field == ref["field_diffs"], f"iter {it}: field diff set mismatch"
        assert got_modified == ref["modified"], f"iter {it}: modified set mismatch"
        assert rep.left_duplicate_keys == ref["left_dup"], f"iter {it}: left dup"
        assert rep.right_duplicate_keys == ref["right_dup"], f"iter {it}: right dup"
        assert rep.left_missing_keys == ref["left_missing"], f"iter {it}: left missing"
        assert rep.right_missing_keys == ref["right_missing"], f"iter {it}: right missing"
        assert rep.compared_columns == ref["compared_columns"], f"iter {it}: columns"
        for k in rep.added_keys:
            assert k in ref["added"]
        for k in rep.removed_keys:
            assert k in ref["removed"]
    print(f"OK: {rounds} 轮对拍全部通过 (seed={seed})")


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260927
    run_rounds(rounds, seed)
