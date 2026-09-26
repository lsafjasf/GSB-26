"""logengine：结构化日志查询引擎（仅标准库）。

用法示例：

    from logengine import Store, query

    store = Store()
    for rec in records:          # records 按 ts 升序
        store.append(rec)
        if 满一个块:
            store.seal_block()
    store.flush()

    result = query(store, "SELECT * WHERE service = 'api' AND ts >= 100 ORDER BY ts DESC LIMIT 10")
    result.data              # 命中的记录（或计数/分组结果）
    result.metrics           # 扫描块数 / 跳过块数 / 扫描记录数
"""

from .engine import Metrics, Result, execute, full_scan, full_scan_query, query
from .parser import Condition, ParseError, Query, parse
from .storage import Block, BlockStats, FieldStats, Store

__all__ = [
    "Store",
    "Block",
    "BlockStats",
    "FieldStats",
    "Query",
    "Condition",
    "ParseError",
    "parse",
    "execute",
    "query",
    "full_scan",
    "full_scan_query",
    "Metrics",
    "Result",
]
