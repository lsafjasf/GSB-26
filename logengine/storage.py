"""分块存储与块级统计。

日志按时间分块存储。每个块封存时计算统计信息：
- 时间边界 min_ts / max_ts
- 每个字段的取值分布（离散值集合 + 数值 min/max）

查询引擎利用这些统计在块级下推过滤条件，跳过无关块。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class FieldStats:
    """单个字段在一个块内的分布统计。"""

    values: Set[Any] = field(default_factory=set)
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    count: int = 0

    def add(self, value: Any) -> None:
        self.count += 1
        self.values.add(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            v = float(value)
            if self.min_val is None or v < self.min_val:
                self.min_val = v
            if self.max_val is None or v > self.max_val:
                self.max_val = v


@dataclass
class BlockStats:
    min_ts: float
    max_ts: float
    row_count: int
    fields: Dict[str, FieldStats]


@dataclass
class Block:
    block_id: int
    records: List[Dict[str, Any]]
    stats: BlockStats


class Store:
    """按时间分块的日志存储。

    使用方式：先 append 记录，再 seal_block() 封存一个块。
    封存要求：块内记录按 ts 升序，且下一条记录的 ts 必须严格大于
    已封存块的最大 ts（保证块间时间区间不重叠且单调递增，
    这是“按时间倒序取前 N 条并提前终止”正确性的前提）。
    """

    def __init__(self) -> None:
        self._blocks: List[Block] = []
        self._pending: List[Dict[str, Any]] = []

    def append(self, record: Dict[str, Any]) -> None:
        if "ts" not in record:
            raise ValueError("record must contain 'ts'")
        if not isinstance(record["ts"], (int, float)) or isinstance(record["ts"], bool):
            raise ValueError("'ts' must be a number")
        if self._blocks and record["ts"] <= self._blocks[-1].stats.max_ts:
            raise ValueError(
                "record ts must be strictly greater than the last sealed block's max_ts"
            )
        if self._pending and record["ts"] < self._pending[-1]["ts"]:
            raise ValueError("records within a block must be appended in ascending ts order")
        self._pending.append(dict(record))

    def seal_block(self) -> Optional[Block]:
        if not self._pending:
            return None
        records = self._pending
        self._pending = []
        block = Block(
            block_id=len(self._blocks),
            records=records,
            stats=build_stats(records),
        )
        self._blocks.append(block)
        return block

    def flush(self) -> None:
        self.seal_block()

    @property
    def blocks(self) -> List[Block]:
        return list(self._blocks)

    def total_rows(self) -> int:
        return sum(b.stats.row_count for b in self._blocks)


def build_stats(records: List[Dict[str, Any]]) -> BlockStats:
    fields: Dict[str, FieldStats] = {}
    for rec in records:
        for key, value in rec.items():
            stats = fields.get(key)
            if stats is None:
                stats = fields[key] = FieldStats()
            stats.add(value)
    return BlockStats(
        min_ts=records[0]["ts"],
        max_ts=records[-1]["ts"],
        row_count=len(records),
        fields=fields,
    )
