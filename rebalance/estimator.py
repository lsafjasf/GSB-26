"""迁移数据量与耗时估算。

公式：
  迁移分区数  M = sum(max(0, target[n] - current[n]))   （理论下限，计划即达到）
  迁移数据量  D = M * avg_partition_bytes  （或 sum(各被迁分区实际大小)）
  耗时        T = D / (rate * parallelism) + ceil(M / parallelism) * cutover
  其中 rate 为单流拷贝带宽，cutover 为单次切换（临界区补拷+落盘）开销。
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from .planner import balanced_targets


def estimate_move_count(num_partitions: int,
                        old_assignment: Sequence[str],
                        new_nodes: Sequence[str]) -> int:
    current = Counter(old_assignment)
    target = balanced_targets(num_partitions, list(new_nodes))
    return sum(max(0, target[n] - current.get(n, 0)) for n in new_nodes)


def estimate_data_bytes(num_moves: int, avg_partition_bytes: float) -> float:
    return num_moves * avg_partition_bytes


def estimate_time_seconds(data_bytes: float, rate_bps: float,
                          num_moves: int, cutover_seconds: float,
                          parallelism: int = 1) -> float:
    if rate_bps <= 0:
        raise ValueError("rate must be positive")
    import math
    return (data_bytes / (rate_bps * parallelism)
            + math.ceil(num_moves / parallelism) * cutover_seconds)
