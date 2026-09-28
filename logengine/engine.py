"""查询执行引擎。

执行流程：
1. 规划：用块统计做条件下推，得到候选块（被跳过的块不计入扫描）。
2. 执行：
   - ORDER BY ts DESC + LIMIT：候选块按 max_ts 降序逐块扫描，
     凑满 N 条立即终止，剩余候选块记为 skipped_limit（未扫描）。
   - 其他查询：按块封存顺序扫描全部候选块。
3. 聚合：GROUP BY 时按键分组计数（排序输出保证确定性）。

排序键为 (ts, 写入序号)：块内第 i 条记录的写入序号为
block.base_seq + i。DESC 时两键同向取反，即并列时间戳组内
后写入的记录在前——与提前终止分支 reversed(block.records)
的取数方向一致，两条路径共用同一个全序。

full_scan() 是禁用一切下推与提前终止的参考实现，用于对拍测试。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .parser import Condition, Query, parse
from .planner import plan
from .storage import Store


@dataclass
class Metrics:
    total_blocks: int = 0
    scanned_blocks: int = 0
    pruned_blocks: int = 0         # 被块级统计下推跳过
    skipped_limit_blocks: int = 0  # LIMIT 提前终止而未触及
    scanned_records: int = 0

    @property
    def skipped_blocks(self) -> int:
        return self.pruned_blocks + self.skipped_limit_blocks


@dataclass
class Result:
    data: Any
    metrics: Metrics


def _cmp_values(a: Any, b: Any) -> Optional[int]:
    """同类型比较；跨类型返回 None（视为不匹配）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        if isinstance(a, bool) and isinstance(b, bool):
            return (a > b) - (a < b)
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    if a is None and b is None:
        return 0
    return None


def record_matches(record: Dict[str, Any], conditions: List[Condition]) -> bool:
    for cond in conditions:
        if cond.field not in record:
            return False
        cmp = _cmp_values(record[cond.field], cond.value)
        if cmp is None:
            return False
        if cond.op == "=":
            ok = cmp == 0
        elif cond.op == "!=":
            ok = cmp != 0
        elif cond.op == "<":
            ok = cmp < 0
        elif cond.op == "<=":
            ok = cmp <= 0
        elif cond.op == ">":
            ok = cmp > 0
        elif cond.op == ">=":
            ok = cmp >= 0
        else:
            raise ValueError(f"unknown operator {cond.op!r}")
        if not ok:
            return False
    return True


def _validate(query: Query) -> None:
    if query.order_by is not None and query.order_by != "ts":
        raise ValueError("ORDER BY only supports the 'ts' field")
    if query.group_by is not None and query.select != "count":
        raise ValueError("GROUP BY requires SELECT COUNT(*)")
    if query.group_by is not None and query.order_by is not None:
        raise ValueError("GROUP BY cannot be combined with ORDER BY")


def _group_key(value: Any) -> str:
    return f"{type(value).__name__}:{value!r}"


def _run(query: Query, blocks, pushdown: bool) -> Tuple[Any, Metrics]:
    metrics = Metrics(total_blocks=len(blocks))
    if pushdown:
        candidates, pruned = plan(blocks, query)
        metrics.pruned_blocks = pruned
    else:
        candidates = list(blocks)

    if query.limit == 0 and query.select == "*":
        metrics.skipped_limit_blocks = len(candidates)
        return [], metrics

    early_termination = (
        pushdown
        and query.limit is not None
        and query.order_by == "ts"
        and query.order_desc
        and query.select == "*"
        and query.group_by is None
    )

    if early_termination:
        ordered = sorted(candidates, key=lambda b: b.stats.max_ts, reverse=True)
        rows: List[Dict[str, Any]] = []
        for idx, block in enumerate(ordered):
            metrics.scanned_blocks += 1
            metrics.scanned_records += block.stats.row_count
            for rec in reversed(block.records):
                if record_matches(rec, query.conditions):
                    rows.append(rec)
                    if len(rows) >= query.limit:
                        metrics.skipped_limit_blocks = len(ordered) - idx - 1
                        return rows, metrics
        return rows, metrics

    matched: List[Tuple[int, Dict[str, Any]]] = []
    for block in candidates:
        metrics.scanned_blocks += 1
        metrics.scanned_records += block.stats.row_count
        for offset, rec in enumerate(block.records):
            if record_matches(rec, query.conditions):
                matched.append((block.base_seq + offset, rec))

    if query.select == "count":
        if query.group_by is None:
            return len(matched), metrics
        groups: Dict[Any, int] = {}
        for _, rec in matched:
            key = rec.get(query.group_by)
            groups[key] = groups.get(key, 0) + 1
        rows = sorted(groups.items(), key=lambda kv: _group_key(kv[0]))
        return rows, metrics

    if query.order_by == "ts":
        matched.sort(key=lambda p: (p[1]["ts"], p[0]), reverse=query.order_desc)
    rows = [rec for _, rec in matched]
    if query.limit is not None:
        rows = rows[: query.limit]
    return rows, metrics


def execute(store: Store, query: Query) -> Result:
    """带块级下推与提前终止的正常执行。"""
    _validate(query)
    data, metrics = _run(query, store.blocks, pushdown=True)
    return Result(data=data, metrics=metrics)


def full_scan(store: Store, query: Query) -> Result:
    """参考实现：扫描全部块、不做任何下推与提前终止。用于对拍。"""
    _validate(query)
    data, metrics = _run(query, store.blocks, pushdown=False)
    return Result(data=data, metrics=metrics)


def query(store: Store, text: str) -> Result:
    """解析并执行查询字符串。"""
    return execute(store, parse(text))


def full_scan_query(store: Store, text: str) -> Result:
    return full_scan(store, parse(text))
