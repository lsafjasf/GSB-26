"""tablediff: 表级差异对比库（仅标准库）。

按主键对齐比较两张表，输出新增/删除/字段级修改三类差异，
并对主键重复、主键缺失、表结构不一致给出明确报告。
"""

from .core import (
    Column,
    Table,
    DiffConfig,
    DiffReport,
    SchemaIssue,
    diff_tables,
    infer_schema,
)

__all__ = [
    "Column",
    "Table",
    "DiffConfig",
    "DiffReport",
    "SchemaIssue",
    "diff_tables",
    "infer_schema",
]

__version__ = "0.1.0"
