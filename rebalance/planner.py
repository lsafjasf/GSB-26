"""再平衡方案计算。

给定旧节点集合、新节点集合与分区数，输出迁移计划。
迁移数达到理论下限：lower_bound = sum(max(0, target[n] - current[n]))，
即每个新归属节点"缺口"的总和；每次迁移恰好填补一个缺口，因此计划最优。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class Move:
    partition: int
    src: str
    dst: str


@dataclass
class Plan:
    num_partitions: int
    old_nodes: List[str]
    new_nodes: List[str]
    old_assignment: List[str]
    new_assignment: List[str]
    moves: List[Move]
    lower_bound: int

    @property
    def num_moves(self) -> int:
        return len(self.moves)

    def summary(self) -> str:
        lines = [
            f"partitions={self.num_partitions} "
            f"nodes: {len(self.old_nodes)} -> {len(self.new_nodes)}",
            f"moves={self.num_moves} lower_bound={self.lower_bound} "
            f"(ratio={self.num_moves / self.lower_bound if self.lower_bound else 1.0:.3f})",
        ]
        old_cnt = Counter(self.old_assignment)
        new_cnt = Counter(self.new_assignment)
        for n in self.old_nodes + [n for n in self.new_nodes if n not in old_cnt]:
            lines.append(
                f"  {n}: {old_cnt.get(n, 0)} -> {new_cnt.get(n, 0)}"
            )
        return "\n".join(lines)


def _validate(num_partitions: int, nodes: Sequence[str]) -> None:
    if num_partitions < 1:
        raise ValueError("num_partitions must be >= 1")
    if not nodes:
        raise ValueError("node set must be non-empty")
    if len(set(nodes)) != len(nodes):
        raise ValueError("node ids must be unique")


def balanced_targets(num_partitions: int, nodes: Sequence[str]) -> Dict[str, int]:
    """每个节点应持有的分区数（尽量均匀，差值 <= 1）。"""
    _validate(num_partitions, nodes)
    base, rem = divmod(num_partitions, len(nodes))
    return {n: base + (1 if i < rem else 0) for i, n in enumerate(nodes)}


def initial_assignment(num_partitions: int, nodes: Sequence[str]) -> List[str]:
    """生成初始均衡归属：assignment[pid] = owner。"""
    targets = balanced_targets(num_partitions, nodes)
    assignment: List[str] = []
    for n in nodes:
        assignment.extend([n] * targets[n])
    return assignment


def moves_lower_bound(old_assignment: Sequence[str],
                      new_nodes: Sequence[str]) -> int:
    """理论下限：新节点集合中每个节点的缺口之和。"""
    current = Counter(old_assignment)
    target = balanced_targets(len(old_assignment), list(new_nodes))
    return sum(max(0, target[n] - current.get(n, 0)) for n in new_nodes)


def compute_plan(num_partitions: int,
                 old_nodes: Sequence[str],
                 new_nodes: Sequence[str],
                 old_assignment: Sequence[str] = None) -> Plan:
    """计算迁移计划，迁移数恰好等于理论下限。

    策略：保留节点尽量留住其分区（不超过目标数），
    被移除节点的分区与保留节点的超额分区进入空闲池，
    按需分配给有缺口的新归属节点。
    """
    _validate(num_partitions, new_nodes)
    if old_assignment is None:
        old_assignment = initial_assignment(num_partitions, old_nodes)
    old_assignment = list(old_assignment)
    if len(old_assignment) != num_partitions:
        raise ValueError("old_assignment length mismatch")

    target = balanced_targets(num_partitions, new_nodes)
    new_assignment: List[str] = [None] * num_partitions  # type: ignore
    kept = Counter()
    free: List[int] = []

    for pid, owner in enumerate(old_assignment):
        if owner in target and kept[owner] < target[owner]:
            new_assignment[pid] = owner
            kept[owner] += 1
        else:
            free.append(pid)

    moves: List[Move] = []
    idx = 0
    for n in new_nodes:
        deficit = target[n] - kept[n]
        for _ in range(deficit):
            pid = free[idx]
            idx += 1
            new_assignment[pid] = n
            moves.append(Move(pid, old_assignment[pid], n))
    assert idx == len(free), "pool/deficit accounting mismatch"

    lb = moves_lower_bound(old_assignment, new_nodes)
    assert len(moves) == lb, "plan must be optimal"

    return Plan(
        num_partitions=num_partitions,
        old_nodes=list(old_nodes),
        new_nodes=list(new_nodes),
        old_assignment=old_assignment,
        new_assignment=new_assignment,  # type: ignore
        moves=moves,
        lower_bound=lb,
    )
