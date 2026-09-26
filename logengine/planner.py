"""查询规划：把过滤条件下推到块级，利用块统计跳过无关块。

下推规则（对合取条件中的每一个，只要能证明块内不可能有匹配记录，就跳过整块）：
- ts 条件：与块的时间边界 [min_ts, max_ts] 求交，区间为空则跳过。
- 等值 field = v：v 不在该块该字段的取值集合中则跳过。
- 不等 field != v：该块该字段只可能取 v（取值集合为 {v}）则跳过。
- 数值范围 field </<=/>/>= v：与块内该字段的 [min_val, max_val] 求交，为空则跳过。
- 字段在块统计中不存在（块内没有任何记录含该字段）：除 != 外均可跳过；
  != 无法据此判断，保守保留。
"""

from typing import Any, List, Optional, Tuple

from .parser import Condition, Query
from .storage import Block


def time_bounds(conditions: List[Condition]) -> Tuple[Optional[float], Optional[float], bool, bool]:
    """从 ts 相关条件提取 (lo, hi, lo_inclusive, hi_inclusive)。"""
    lo = hi = None
    lo_inc = hi_inc = True
    for cond in conditions:
        if cond.field != "ts":
            continue
        v = cond.value
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        v = float(v)
        if cond.op == ">":
            if lo is None or v > lo or (v == lo and lo_inc):
                lo, lo_inc = v, False
        elif cond.op == ">=":
            if lo is None or v > lo:
                lo, lo_inc = v, True
        elif cond.op == "<":
            if hi is None or v < hi or (v == hi and hi_inc):
                hi, hi_inc = v, False
        elif cond.op == "<=":
            if hi is None or v < hi:
                hi, hi_inc = v, True
        elif cond.op == "=":
            if lo is None or v > lo:
                lo, lo_inc = v, True
            if hi is None or v < hi:
                hi, hi_inc = v, True
    return lo, hi, lo_inc, hi_inc


def block_matches_time(block: Block, bounds: Tuple[Optional[float], Optional[float], bool, bool]) -> bool:
    lo, hi, lo_inc, hi_inc = bounds
    b_lo, b_hi = block.stats.min_ts, block.stats.max_ts
    if lo is not None:
        if b_hi < lo or (b_hi == lo and not lo_inc):
            return False
    if hi is not None:
        if b_lo > hi or (b_lo == hi and not hi_inc):
            return False
    return True


def block_matches_field(block: Block, cond: Condition) -> bool:
    """判断块是否可能包含满足该字段条件的记录（保守：不确定时返回 True）。"""
    stats = block.stats.fields.get(cond.field)
    if stats is None:
        return cond.op == "!="
    v = cond.value
    if cond.op == "=":
        return v in stats.values
    if cond.op == "!=":
        return not (stats.values == {v})
    # 范围条件：仅对数值用 min/max 下推，其余保守保留
    if isinstance(v, (int, float)) and not isinstance(v, bool) and stats.min_val is not None:
        lo, hi = stats.min_val, stats.max_val
        if cond.op == "<":
            return lo < v
        if cond.op == "<=":
            return lo <= v
        if cond.op == ">":
            return hi > v
        if cond.op == ">=":
            return hi >= v
    return True


def block_matches(block: Block, query: Query) -> bool:
    if not block_matches_time(block, time_bounds(query.conditions)):
        return False
    for cond in query.conditions:
        if cond.field == "ts":
            continue
        if not block_matches_field(block, cond):
            return False
    return True


def plan(blocks: List[Block], query: Query) -> Tuple[List[Block], int]:
    """返回 (候选块列表, 被跳过的块数)。"""
    candidates = [b for b in blocks if block_matches(b, query)]
    return candidates, len(blocks) - len(candidates)
