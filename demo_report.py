#!/usr/bin/env python3
"""生成一份覆盖全部特性的报告样例（写出到 sample_report.txt）。"""

from tablediff import Column, DiffConfig, Table, diff_tables

left_schema = [
    Column("id", "int"),
    Column("name", "str"),
    Column("price", "float"),
    Column("updated_at", "str"),
    Column("legacy_flag", "int"),
]
right_schema = [
    Column("id", "int"),
    Column("name", "str"),
    Column("price", "float"),
    Column("updated_at", "str"),
    Column("score", "str"),  # 仅右表有
]

left = Table(left_schema, [
    (1, "apple", 9.99, "2026-09-01", 0),
    (2, "banana", 3.50, "2026-09-01", 1),
    (3, "cherry", 12.00, "2026-09-02", 0),
    (4, "date", 7.25, "2026-09-02", 0),
    (5, "fig", 5.00, "2026-09-03", 0),
    (5, "fig-dup", 5.00, "2026-09-03", 0),   # 主键重复
    (None, "ghost", 0.00, "2026-09-03", 0),  # 主键缺失
])
right = Table(right_schema, [
    (1, "apple", 9.995, "2026-09-20", "a"),   # 容差内
    (2, "banana", 4.20, "2026-09-20", "b"),   # 超出容差 -> 修改
    (3, "cherry", 12.00, "2026-09-20", "c"),  # 仅忽略列不同
    (4, "DATE", 7.25, "2026-09-20", "d"),     # name 修改
    (6, "grape", 8.00, "2026-09-20", "e"),    # 新增
    (None, "ghost2", 0.00, "2026-09-20", "f"),
])

cfg = DiffConfig(
    key_columns=("id",),
    ignore_columns=("updated_at",),
    tolerances={"price": (0.01, 0.0)},
)
rep = diff_tables(left, right, cfg)
text = rep.to_text()
print(text)
with open("sample_report.txt", "w", encoding="utf-8") as f:
    f.write(text + "\n")
print("\n已写出 sample_report.txt")
