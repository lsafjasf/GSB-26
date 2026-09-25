"""MVCC 内存存储：快照隔离 + 多版本 + 基于最小活跃快照的版本回收。

仅使用 Python 标准库。

核心概念
--------
- 全局单调递增的版本计数器 ``_clock``。每次写事务提交时分配一个
  新的提交版本号 (commit_version)。
- 每个 key 维护一条版本链 ``[(commit_version, value), ...]``，
  按 commit_version 升序排列；value 为 ``_TOMBSTONE`` 表示删除。
- 事务在 begin 时获得快照版本 ``snapshot = 当前 clock``，
  只能看到 ``commit_version <= snapshot`` 的最新版本（快照隔离）。
- 写事务的修改先进入私有写缓冲，提交时做写写冲突检测
  （first-committer-wins，冲突即拒绝），通过后为每个写生成新版本。
- 版本回收 (GC)：设 ``min_snap`` 为所有活跃事务的最小快照
  （无活跃事务时取当前 clock）。对每个 key，保留满足
  ``commit_version <= min_snap`` 的最新版本及其之后的所有版本，
  更早的版本对任何现存或未来的事务都不可见，可以安全删除。
"""

from __future__ import annotations

import threading

_TOMBSTONE = object()  # 删除标记


class WriteConflictError(Exception):
    """写写冲突：目标 key 在事务快照之后被其他事务提交了新版本。"""


class TransactionStateError(Exception):
    """事务状态非法：如对已结束的事务继续操作。"""


def is_visible(commit_version: int, snapshot: int) -> bool:
    """可见性规则（唯一的判定入口，测试直接断言它）。

    版本对快照可见  <=>  版本的提交版本号 <= 读快照版本号。
    推论：读事务看不到自己 begin 之后提交的版本
    （那些版本的 commit_version 一定 > snapshot）。
    """
    assert commit_version >= 1, "版本号从 1 开始"
    assert snapshot >= 0, "快照版本号非负"
    return commit_version <= snapshot


class Version:
    __slots__ = ("commit_version", "value")

    def __init__(self, commit_version: int, value):
        self.commit_version = commit_version
        self.value = value

    @property
    def deleted(self) -> bool:
        return self.value is _TOMBSTONE

    def __repr__(self):  # pragma: no cover - 调试用
        v = "TOMBSTONE" if self.deleted else self.value
        return f"Version(v{self.commit_version}, {v!r})"


class Transaction:
    """读写事务。读按 begin 时的快照；写先落私有缓冲，提交时生效。"""

    __slots__ = ("_store", "snapshot", "id", "_writes", "_state")

    def __init__(self, store: "MVCCStore", tx_id: int, snapshot: int):
        self._store = store
        self.snapshot = snapshot
        self.id = tx_id
        self._writes: dict = {}  # key -> value 或 _TOMBSTONE
        self._state = "active"   # active | committed | aborted

    # -- 读 ------------------------------------------------------------
    def get(self, key, default=None):
        """按快照读取：先查自己的写缓冲（read-your-own-writes），
        再沿版本链找 commit_version <= snapshot 的最新版本。"""
        self._check_active()
        if key in self._writes:
            value = self._writes[key]
            return default if value is _TOMBSTONE else value
        return self._store._read_at(self.snapshot, key, default)

    # -- 写 ------------------------------------------------------------
    def put(self, key, value):
        self._check_active()
        self._writes[key] = value

    def delete(self, key):
        self._check_active()
        self._writes[key] = _TOMBSTONE

    # -- 结束 -----------------------------------------------------------
    def commit(self):
        """提交：写写冲突检测（first-committer-wins，冲突抛异常并回滚）。

        冲突条件：写集合中的 key 存在 commit_version > 本事务快照的版本，
        即 begin 之后有别的写事务改过同一个 key。
        """
        self._check_active()
        try:
            self._store._commit(self)
        except WriteConflictError:
            self._writes.clear()
            self._state = "aborted"
            raise
        self._state = "committed"

    def rollback(self):
        self._check_active()
        self._store._finish(self)
        self._writes.clear()
        self._state = "aborted"

    @property
    def state(self) -> str:
        return self._state

    def _check_active(self):
        if self._state != "active":
            raise TransactionStateError(f"事务 {self.id} 已结束（{self._state}）")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._state == "active":
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        return False


class MVCCStore:
    """多版本并发控制内存存储（线程安全）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._clock = 0                      # 全局版本计数器 = 最新提交版本
        self._tx_seq = 0                     # 事务 id 计数器
        self._data: dict = {}                # key -> [Version, ...] 升序
        self._active: dict = {}              # tx_id -> snapshot

    # -- 事务生命周期 ----------------------------------------------------
    def begin(self) -> Transaction:
        with self._lock:
            self._tx_seq += 1
            tx = Transaction(self, self._tx_seq, self._clock)
            self._active[tx.id] = tx.snapshot
            return tx

    def _commit(self, tx: Transaction):
        with self._lock:
            # 写写冲突检测：快照之后有人改过同一 key 则拒绝提交
            for key in tx._writes:
                chain = self._data.get(key)
                if chain and chain[-1].commit_version > tx.snapshot:
                    self._finish(tx)
                    raise WriteConflictError(
                        f"key {key!r}: 最新版本 v{chain[-1].commit_version} "
                        f"> 事务快照 v{tx.snapshot}"
                    )
            if tx._writes:
                self._clock += 1
                cv = self._clock
                for key, value in tx._writes.items():
                    self._data.setdefault(key, []).append(Version(cv, value))
            self._finish(tx)

    def _finish(self, tx: Transaction):
        self._active.pop(tx.id, None)

    # -- 快照读 -----------------------------------------------------------
    def _read_at(self, snapshot: int, key, default=None):
        with self._lock:
            chain = self._data.get(key)
            if not chain:
                return default
            # 链短，线性倒扫即可；找 commit_version <= snapshot 的最新版本
            for ver in reversed(chain):
                if is_visible(ver.commit_version, snapshot):
                    return default if ver.deleted else ver.value
            return default

    # -- 版本回收 ----------------------------------------------------------
    def min_active_snapshot(self) -> int:
        """活跃事务的最小快照；无活跃事务时返回当前 clock。"""
        with self._lock:
            return min(self._active.values()) if self._active else self._clock

    def gc(self) -> int:
        """回收不再可见的旧版本，返回删除的版本数。

        回收条件：设 min_snap = min_active_snapshot()。对每个 key，
        找到满足 commit_version <= min_snap 的最新版本 V_keep，
        V_keep 之前的所有版本对任何活跃事务（快照 >= min_snap）和
        未来事务（快照 >= 当前 clock >= min_snap）都不可见，可删除。
        长事务会拉低 min_snap，从而阻止其可见的旧版本被回收。
        """
        removed = 0
        with self._lock:
            min_snap = self.min_active_snapshot()
            for key in list(self._data):
                chain = self._data[key]
                # keep_idx：最后一个 commit_version <= min_snap 的下标
                keep_idx = -1
                for i, ver in enumerate(chain):
                    if ver.commit_version <= min_snap:
                        keep_idx = i
                    else:
                        break
                if keep_idx > 0:
                    removed += keep_idx
                    del chain[:keep_idx]
                if not chain:
                    del self._data[key]
        return removed

    # -- 观测接口（测试 / 基准用）-----------------------------------------
    def chain_length(self, key) -> int:
        with self._lock:
            return len(self._data.get(key, ()))

    def total_versions(self) -> int:
        with self._lock:
            return sum(len(c) for c in self._data.values())

    def chain_lengths(self) -> dict:
        with self._lock:
            return {k: len(c) for k, c in self._data.items()}
