"""查询计划：纯数据（dataclass + 嵌套 tuple），可 pickle，跨进程传递。

Plan 字段语义：
  filter     行过滤谓词（expr），None 表示不过滤
  select     输出列：(别名, expr)；聚合查询中仅用于校验，输出由 group_by+aggs 决定
  group_by   分组键 expr 列表；空列表 + 有 aggs 表示全局（标量）聚合
  aggs       聚合度量列表；非空即为聚合查询
  order_by   排序键，expr 在"输出行"（按别名取列）上求值
  limit      排序后截断
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# 支持的聚合函数
AGG_FUNCS = ("count", "sum", "min", "max", "avg", "count_distinct", "collect_distinct")


@dataclass(frozen=True)
class AggSpec:
    func: str                      # AGG_FUNCS 之一
    expr: Optional[tuple] = None   # 度量表达式；count(*) 时为 None
    alias: str = ""

    def __post_init__(self):
        if self.func not in AGG_FUNCS:
            raise ValueError(f"unknown agg func: {self.func!r}")
        if self.func != "count" and self.expr is None:
            raise ValueError(f"agg {self.func!r} requires an expr")
        if not self.alias:
            object.__setattr__(self, "alias",
                               self.func if self.expr is None else f"{self.func}_0")


@dataclass(frozen=True)
class OrderKey:
    expr: tuple                    # 在输出行上求值（通常 ("col", 别名)）
    desc: bool = False


@dataclass(frozen=True)
class Plan:
    filter: Optional[tuple] = None
    select: Tuple[Tuple[str, tuple], ...] = ()
    group_by: Tuple[tuple, ...] = ()
    aggs: Tuple[AggSpec, ...] = ()
    order_by: Tuple[OrderKey, ...] = ()
    limit: Optional[int] = None

    @property
    def is_aggregate(self) -> bool:
        return bool(self.aggs)

    def output_columns(self):
        if self.is_aggregate:
            cols = [f"key_{i}" for i in range(len(self.group_by))]
            cols += [a.alias for a in self.aggs]
            return cols
        return [alias for alias, _ in self.select]


def count_star(alias="count"):
    return AggSpec("count", None, alias)


def count(expr, alias):
    return AggSpec("count", expr, alias)


def sum_(expr, alias):
    return AggSpec("sum", expr, alias)


def min_(expr, alias):
    return AggSpec("min", expr, alias)


def max_(expr, alias):
    return AggSpec("max", expr, alias)


def avg(expr, alias):
    return AggSpec("avg", expr, alias)


def count_distinct(expr, alias):
    return AggSpec("count_distinct", expr, alias)


def collect_distinct(expr, alias):
    return AggSpec("collect_distinct", expr, alias)
