"""MVCC（多版本并发控制）内存键值存储，仅使用 Python 标准库。

核心概念：
- 每个数据项（key）保存一条版本链，版本带有 [begin_ts, end_ts) 的可见区间。
- 事务开启时获得快照 snapshot_ts（即当前全局提交时间戳），
  读操作只能看到 begin_ts <= snapshot_ts < end_ts 的版本。
- 写事务的修改先写入私有 write set，提交时才生成新版本；
  写-写冲突采用 first-committer-wins（先提交者获胜）策略，后提交者被拒绝。
- 版本回收（GC）基于活跃事务的最小快照 watermark：
  end_ts <= watermark 的版本对所有活跃及未来事务均不可见，可以安全回收。
"""

from __future__ import annotations

import threading

INF = float("inf")
TOMBSTONE = object()  # 删除标记


class WriteConflictError(Exception):
    """两个写事务并发修改同一 key，后提交者被拒绝。"""


class TransactionStateError(Exception):
    """事务状态非法（如已提交后继续读写、只读事务执行写）。"""


class _Version:
    __slots__ = ("value", "begin_ts", "end_ts")

    def __init__(self, value, begin_ts, end_ts=INF):
        self.value = value
        self.begin_ts = begin_ts
        self.end_ts = end_ts

    def visible_at(self, snapshot_ts):
        return self.begin_ts <= snapshot_ts < self.end_ts

    def __repr__(self):
        return f"_Version(value={self.value!r}, begin={self.begin_ts}, end={self.end_ts})"


class Store:
    """MVCC 存储引擎。线程安全（内部使用可重入锁）。"""

    def __init__(self):
        self._data = {}        # key -> list[_Version]，按 begin_ts 从新到旧排列
        self._commit_ts = 0    # 最近一次提交分配的时间戳
        self._txn_seq = 0      # 事务 id 计数器
        self._active = {}      # txn_id -> snapshot_ts（活跃事务表）
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- 事务生命周期

    def begin(self, read_only=False):
        """开启事务。快照为当前全局提交时间戳。"""
        with self._lock:
            self._txn_seq += 1
            txn = Transaction(self, self._txn_seq, self._commit_ts, read_only)
            self._active[txn.txn_id] = txn.snapshot_ts
            return txn

    def _commit(self, txn):
        with self._lock:
            # first-committer-wins：提交时校验 write set 中的每个 key，
            # 若其最新已提交版本的 begin_ts 晚于本事务快照，说明有并发写已提交。
            for key in txn._writes:
                chain = self._data.get(key)
                if chain and chain[0].begin_ts > txn.snapshot_ts:
                    raise WriteConflictError(
                        f"key {key!r}: 快照 {txn.snapshot_ts} 之后已有提交 "
                        f"(begin_ts={chain[0].begin_ts})"
                    )
            self._commit_ts += 1
            ts = self._commit_ts
            for key, value in txn._writes.items():
                chain = self._data.setdefault(key, [])
                if chain:
                    chain[0].end_ts = ts  # 旧版本可见区间结束
                chain.insert(0, _Version(value, ts))
            del self._active[txn.txn_id]
            return ts

    def _finish(self, txn):
        """提交（只读/空写）或回滚时，从事务表中摘除。"""
        with self._lock:
            self._active.pop(txn.txn_id, None)

    # ---------------------------------------------------------------- 读取

    def _lookup(self, key, snapshot_ts):
        """在版本链中选出对 snapshot_ts 可见的版本（可见性规则的唯一实现）。"""
        chain = self._data.get(key)
        if not chain:
            return None
        for version in chain:
            if version.visible_at(snapshot_ts):
                # 可见性断言：读事务绝不能看到自己开始之后才提交的版本。
                assert version.begin_ts <= snapshot_ts, (
                    f"可见性违规: 快照 {snapshot_ts} 读到了 begin_ts="
                    f"{version.begin_ts} 的版本"
                )
                return version
        return None

    # ---------------------------------------------------------------- 版本回收

    def gc_watermark(self):
        """回收水位线：活跃事务的最小快照；无活跃事务时为当前提交时间戳。"""
        with self._lock:
            if self._active:
                return min(self._active.values())
            return self._commit_ts

    def collect_garbage(self):
        """回收所有活跃及未来事务都不可见的版本，返回回收的版本数。

        回收条件：version.end_ts <= watermark。
        证明：任何活跃事务快照 >= watermark，任何未来事务快照 >= 当前
        commit_ts >= watermark；而版本可见要求 snapshot_ts < end_ts <=
        watermark，矛盾，故该版本对谁都不再可见。
        可见区间覆盖 watermark 的版本（end_ts > watermark）必须保留。
        """
        with self._lock:
            watermark = self.gc_watermark()
            reclaimed = 0
            for key in list(self._data):
                chain = self._data[key]
                kept = [v for v in chain if v.end_ts > watermark]
                reclaimed += len(chain) - len(kept)
                assert kept, "最新版本 end_ts 必为 INF，链不可能被清空"
                if (
                    len(kept) == 1
                    and kept[0].value is TOMBSTONE
                    and kept[0].begin_ts <= watermark
                ):
                    # 对所有 >= watermark 的快照该 key 均已删除，可整体移除。
                    del self._data[key]
                    reclaimed += 1
                else:
                    self._data[key] = kept
            return reclaimed

    # ---------------------------------------------------------------- 观测

    def version_stats(self):
        """返回 (key 数, 版本总数, 最大链长, 平均链长)，用于测试与性能分析。"""
        with self._lock:
            lengths = [len(c) for c in self._data.values()]
            if not lengths:
                return (0, 0, 0, 0.0)
            return (
                len(lengths),
                sum(lengths),
                max(lengths),
                sum(lengths) / len(lengths),
            )


class Transaction:
    """事务句柄。支持上下文管理：正常退出提交，异常退出回滚。"""

    ACTIVE = "active"
    COMMITTED = "committed"
    ABORTED = "aborted"

    def __init__(self, store, txn_id, snapshot_ts, read_only):
        self._store = store
        self.txn_id = txn_id
        self.snapshot_ts = snapshot_ts
        self.read_only = read_only
        self.state = Transaction.ACTIVE
        self._writes = {}  # key -> value 或 TOMBSTONE

    # ------------------------------------------------------------ 状态检查

    def _ensure_active(self):
        if self.state != Transaction.ACTIVE:
            raise TransactionStateError(f"事务已{self.state}，不能再操作")

    # ------------------------------------------------------------ 读写

    def get(self, key, default=None):
        """按快照读取。写事务可读到自己的未提交修改（read-your-own-writes）。"""
        self._ensure_active()
        if key in self._writes:
            value = self._writes[key]
            return default if value is TOMBSTONE else value
        with self._store._lock:
            version = self._store._lookup(key, self.snapshot_ts)
        if version is None or version.value is TOMBSTONE:
            return default
        return version.value

    def put(self, key, value):
        self._ensure_active()
        if self.read_only:
            raise TransactionStateError("只读事务不能写")
        with self._store._lock:
            chain = self._store._data.get(key)
            # 提前冲突检测（提交时还会再校验一次，提交时的校验是权威性的）。
            if chain and chain[0].begin_ts > self.snapshot_ts:
                raise WriteConflictError(
                    f"key {key!r}: 快照 {self.snapshot_ts} 之后已有并发提交"
                )
        self._writes[key] = value

    def delete(self, key):
        self._ensure_active()
        if self.read_only:
            raise TransactionStateError("只读事务不能写")
        self.put(key, TOMBSTONE)

    # ------------------------------------------------------------ 提交 / 回滚

    def commit(self):
        """提交。只读或无写事务直接结束；写事务冲突时抛 WriteConflictError 并回滚。"""
        self._ensure_active()
        if self.read_only or not self._writes:
            self._store._finish(self)
            self.state = Transaction.COMMITTED
            return None
        try:
            ts = self._store._commit(self)
        except WriteConflictError:
            self._store._finish(self)
            self.state = Transaction.ABORTED
            raise
        self.state = Transaction.COMMITTED
        return ts

    def rollback(self):
        """回滚：丢弃 write set，对存储不产生任何影响。"""
        self._ensure_active()
        self._writes.clear()
        self._store._finish(self)
        self.state = Transaction.ABORTED

    # ------------------------------------------------------------ 上下文管理

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.state != Transaction.ACTIVE:
            return False
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False
