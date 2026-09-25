"""迁移引擎：按 plan 逐分区搬迁，全程读写不中断，支持崩溃恢复。

单分区状态机（持久化在 journal）：

    pending -> copying -> done

- copying：先在锁外分块拷贝快照（读写照常），随后进入临界区：
  持分区锁 -> 增量补拷 -> epoch+1 -> journal 落盘(owner=dst, done)
  -> 内存路由切换。临界区内完成"补数据 + 切换"，写只会落到一个归属。
- 崩溃恢复：
  * 状态 copying（owner=src）：源数据完整，目标残留数据被重新覆盖拷贝，幂等；
  * 状态 done（owner=dst）：数据已在切换前补全，直接跳过，不会重复迁移。
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Callable, Dict, List, Optional

from .cluster import Cluster
from .journal import Journal
from .planner import Plan


class SimulatedCrash(Exception):
    """测试注入的强杀模拟。"""


class CrashInjector:
    """在第 count 次 checkpoint 之后抛出 SimulatedCrash。"""

    def __init__(self, crash_after: int):
        self.crash_after = crash_after
        self.seen = 0

    def __call__(self, point: str, pid: int) -> None:
        self.seen += 1
        if self.seen > self.crash_after:
            raise SimulatedCrash(f"killed at checkpoint #{self.seen} "
                                 f"({point}, pid={pid})")


def _size_of(items: Dict[str, object]) -> int:
    total = 0
    for k, v in items.items():
        total += len(k)
        total += len(v) if isinstance(v, (bytes, str)) else 8
    return total


class Migrator:
    def __init__(self, cluster: Cluster, journal: Journal,
                 chunk_keys: int = 64,
                 byte_rate: Optional[float] = None,
                 checkpoint_hook: Optional[Callable[[str, int], None]] = None):
        self.cluster = cluster
        self.journal = journal
        self.chunk_keys = chunk_keys
        self.byte_rate = byte_rate          # 模拟带宽（字节/秒），None 表示不限速
        self.checkpoint_hook = checkpoint_hook
        self.stats = {
            "bytes_copied": 0,
            "copies_per_partition": Counter(),
            "started_at": None,
            "finished_at": None,
        }
        self._lock = threading.Lock()

    # ---- 构造 ----

    @classmethod
    def start(cls, cluster: Cluster, plan: Plan, journal_path: str,
              **kw) -> "Migrator":
        from .cluster import Node
        for n in plan.new_nodes:  # 新节点加入集群（模拟扩容）
            cluster.nodes.setdefault(n, Node(n))
        journal = Journal(journal_path)
        journal.save({
            "version": 1,
            "num_partitions": plan.num_partitions,
            "nodes": plan.new_nodes,
            "assignment": list(plan.old_assignment),
            "epochs": list(cluster.epochs),
            "moves": [{"partition": m.partition, "src": m.src, "dst": m.dst,
                       "state": "pending"} for m in plan.moves],
            "complete": False,
        })
        return cls(cluster, journal, **kw)

    @classmethod
    def resume(cls, cluster: Cluster, journal_path: str, **kw) -> "Migrator":
        journal = Journal(journal_path)
        journal.load()
        return cls(cluster, journal, **kw)

    # ---- 进度查询 ----

    def progress(self) -> Dict[str, object]:
        st = self.journal.state
        moves = st["moves"]
        by_state = Counter(m["state"] for m in moves)
        total = len(moves)
        done = by_state.get("done", 0)
        return {
            "total": total,
            "done": done,
            "copying": by_state.get("copying", 0),
            "pending": by_state.get("pending", 0),
            "percent": round(100.0 * done / total, 1) if total else 100.0,
            "complete": st["complete"],
            "bytes_copied": self.stats["bytes_copied"],
        }

    # ---- 主流程 ----

    def run(self) -> None:
        self.stats["started_at"] = self.stats["started_at"] or time.monotonic()
        st = self.journal.state
        for move in st["moves"]:
            self._migrate(move)
        st["complete"] = True
        self.journal.save()
        self.stats["finished_at"] = time.monotonic()

    def _checkpoint(self, point: str, pid: int) -> None:
        if self.checkpoint_hook:
            self.checkpoint_hook(point, pid)

    def _migrate(self, move: Dict[str, object]) -> None:
        pid = move["partition"]
        if move["state"] == "done":
            return  # 已迁移：跳过，保证不重复
        src, dst = move["src"], move["dst"]

        # 阶段 1：锁外分块拷贝（读写不中断）
        move["state"] = "copying"
        self.journal.save()
        self._checkpoint("copying", pid)
        self._copy(pid, src, dst)

        # 阶段 2：临界区内增量补拷 + 原子切换（先落盘再切内存）
        with self.cluster.locks[pid]:
            self._copy(pid, src, dst, delta_only=True)  # 补拷拷贝期间的增量写
            new_epoch = self.cluster.epochs[pid] + 1
            self.cluster.epochs[pid] = new_epoch
            # fencing：新旧主同时提升 epoch，旧主即刻拒绝迟到写
            self.cluster.nodes[src].fence(pid, new_epoch)
            self.cluster.nodes[dst].fence(pid, new_epoch)
            st = self.journal.state
            st["assignment"][pid] = dst
            st["epochs"][pid] = new_epoch
            move["state"] = "done"
            self.journal.save()
            self.cluster.switch_locked(pid, dst)
        self._checkpoint("done", pid)

        # 阶段 3：清理源副本（best-effort；崩溃后由 recover 补做）
        self.cluster.nodes[src].delete_partition(pid)

    def _copy(self, pid: int, src: str, dst: str,
              delta_only: bool = False) -> None:
        src_node = self.cluster.nodes[src]
        dst_node = self.cluster.nodes[dst]
        snap = src_node.snapshot(pid)
        if delta_only:  # 只补拷目标端缺失/过期的 key
            snap = {k: v for k, v in snap.items()
                    if dst_node.read(pid, k) != v}
        if not snap:
            return
        items = list(snap.items())
        epoch = self.cluster.epochs[pid]
        for i in range(0, len(items), self.chunk_keys):
            chunk = dict(items[i:i + self.chunk_keys])
            nbytes = _size_of(chunk)
            if self.byte_rate:
                time.sleep(nbytes / self.byte_rate)
            dst_node.apply(pid, chunk, epoch)
            with self._lock:
                self.stats["bytes_copied"] += nbytes
                self.stats["copies_per_partition"][pid] += 1


def recover_cluster(journal_path: str,
                    nodes: Dict[str, "Node"]) -> "tuple[Cluster, Migrator]":
    """崩溃后重启：从 journal 重建路由表，返回 (cluster, migrator)。

    nodes 为存活节点（真实部署中节点是独立进程，数据不丢；
    模拟中复用原 Node 对象）。
    """
    from .cluster import Node, Cluster as _Cluster
    journal = Journal(journal_path)
    st = journal.load()
    node_map = nodes
    cluster = Cluster.__new__(Cluster)
    import threading as _t
    cluster.nodes = node_map
    cluster.num_partitions = st["num_partitions"]
    cluster.locks = [_t.Lock() for _ in range(st["num_partitions"])]
    cluster.table = list(st["assignment"])
    cluster.epochs = list(st["epochs"])
    cluster.ownership_log = [[(cluster.table[p], cluster.epochs[p])]
                             for p in range(st["num_partitions"])]
    # 补做被崩溃打断的源副本清理
    for move in st["moves"]:
        if move["state"] == "done":
            src = move["src"]
            if src != cluster.table[move["partition"]]:
                node_map[src].delete_partition(move["partition"])
    migrator = Migrator(cluster, journal)
    return cluster, migrator
