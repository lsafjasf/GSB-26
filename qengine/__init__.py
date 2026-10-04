"""分片并行查询引擎（纯标准库）。"""

from .plan import (AGG_FUNCS, AggSpec, OrderKey, Plan, avg, collect_distinct,
                   count, count_distinct, count_star, max_, min_, sum_)
from .table import (Schema, list_shards, read_csv_shard, split_evenly,
                    write_csv_shard)
from .engine import (assert_rows_equal, row_signature, run, run_parallel,
                     run_serial, scan_shard)

__all__ = [
    "AGG_FUNCS", "AggSpec", "OrderKey", "Plan",
    "avg", "collect_distinct", "count", "count_distinct", "count_star",
    "max_", "min_", "sum_",
    "Schema", "list_shards", "read_csv_shard", "split_evenly",
    "write_csv_shard",
    "assert_rows_equal", "row_signature", "run", "run_parallel",
    "run_serial", "scan_shard",
]
