"""集群模拟：节点、路由表、带 fencing 的读写路径。

单主保证：
- 每个分区有一把锁，路由查询 + 写落在同一临界区内，切换是原子的；
- 每个分区有单调递增的 epoch（fencing token），节点拒绝携带
  过期 epoch 的写，杜绝切换瞬间的双主写入。
读一致性：读同样走分区锁，因此读总能看到最近一次已确认的写。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Sequence

from .planner import initial_assignment


class StaleEpochError(Exception):
    """写入携带的 epoch 落后于节点已知的 epoch，被拒绝（fencing）。"""


class Node:
    """存储节点：保存分区数据与每个分区的 fencing epoch。"""

    def __init__(self, node_id: str):
        self.id = node_id
        self.data: Dict[int, Dict[str, object]] = {}
        self.epochs: Dict[int, int] = {}
        self.lock = threading.Lock()
        self.rejected_stale_writes = 0

    def write(self, pid: int, key: str, value: object, epoch: int) -> None:
        with self.lock:
            if epoch < self.epochs.get(pid, 0):
                self.rejected_stale_writes += 1
                raise StaleEpochError(
                    f"node {self.id} pid {pid}: epoch {epoch} < "
                    f"{self.epochs[pid]}")
            self.epochs[pid] = epoch
            self.data.setdefault(pid, {})[key] = value

    def read(self, pid: int, key: str) -> object:
        with self.lock:
            return self.data.get(pid, {}).get(key)

    def snapshot(self, pid: int) -> Dict[str, object]:
        with self.lock:
            return dict(self.data.get(pid, {}))

    def apply(self, pid: int, items: Dict[str, object], epoch: int) -> None:
        """迁移器批量灌入数据（幂等：同 key 覆盖）。"""
        with self.lock:
            if epoch < self.epochs.get(pid, 0):
                self.rejected_stale_writes += 1
                raise StaleEpochError(
                    f"node {self.id} pid {pid}: apply epoch {epoch} stale")
            self.epochs[pid] = epoch
            self.data.setdefault(pid, {}).update(items)

    def delete_partition(self, pid: int) -> None:
        with self.lock:
            self.data.pop(pid, None)
            self.epochs.pop(pid, None)

    def fence(self, pid: int, epoch: int) -> None:
        """将本节点上该分区的 fencing epoch 提升到 epoch（只升不降）。"""
        with self.lock:
            if epoch > self.epochs.get(pid, 0):
                self.epochs[pid] = epoch


class Cluster:
    """路由层：客户端所有读写都经过它。"""

    def __init__(self, node_ids: Sequence[str], num_partitions: int,
                 assignment: Optional[Sequence[str]] = None,
                 epochs: Optional[Sequence[int]] = None,
                 nodes: Optional[Dict[str, Node]] = None):
        self.nodes: Dict[str, Node] = nodes if nodes is not None else {
            n: Node(n) for n in node_ids}
        self.num_partitions = num_partitions
        self.locks = [threading.Lock() for _ in range(num_partitions)]
        if assignment is None:
            assignment = initial_assignment(num_partitions, list(node_ids))
        self.table: List[str] = list(assignment)
        self.epochs: List[int] = list(epochs) if epochs else [1] * num_partitions
        # 归属变更日志：(owner, epoch)，用于测试验证任意时刻单主
        self.ownership_log: List[List[tuple]] = [
            [(self.table[p], self.epochs[p])] for p in range(num_partitions)]

    # ---- 客户端接口 ----

    def write(self, pid: int, key: str, value: object) -> None:
        with self.locks[pid]:
            owner = self.table[pid]
            self.nodes[owner].write(pid, key, value, self.epochs[pid])

    def increment(self, pid: int, key: str) -> int:
        with self.locks[pid]:
            owner = self.table[pid]
            node = self.nodes[owner]
            cur = node.read(pid, key) or 0
            node.write(pid, key, cur + 1, self.epochs[pid])
            return cur + 1

    def read(self, pid: int, key: str) -> object:
        with self.locks[pid]:
            owner = self.table[pid]
            return self.nodes[owner].read(pid, key)

    def dump(self, pid: int) -> Dict[str, object]:
        with self.locks[pid]:
            return self.nodes[self.table[pid]].snapshot(pid)

    # ---- 迁移器专用（调用方须持有 locks[pid]） ----

    def switch_locked(self, pid: int, dst: str) -> None:
        self.table[pid] = dst
        self.ownership_log[pid].append((dst, self.epochs[pid]))
