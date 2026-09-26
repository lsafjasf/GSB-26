"""事务撤销日志与回滚库（仅标准库）。

设计要点：
- 数据与撤销日志都持久化在 SQLite（WAL + synchronous=FULL）中。
- 每次写操作记录一条撤销日志（旧值 / 是否原本存在），与数据修改在同一个
  SQLite 事务中落盘，保证二者原子一致。
- 回滚按 seq 逆序应用撤销日志，每应用一批就把对应日志标记为 applied 并提交，
  因此回滚可断点续滚、幂等（已 applied 的条目跳过，且恢复旧值本身幂等）。
- 进程崩溃后重新打开库时，recover 会把 ACTIVE / ROLLING_BACK 状态的事务
  继续回滚到 ROLLED_BACK。
- 并发可见性：事务写入的键在事务结束前（提交或回滚完成）被锁定；
  其他线程读这些键会阻塞，直到事务结束，因此永远读不到未提交或
  回滚到一半的中间状态。回滚全程持有存储级锁，对读者原子可见。
"""

from __future__ import annotations

import pickle
import sqlite3
import threading

_TOMBSTONE = object()  # 表示“删除该键”的待落盘写

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS tx (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE','COMMITTED','ROLLING_BACK','ROLLED_BACK'))
);
CREATE TABLE IF NOT EXISTS undo (
    tx_id     INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    key       TEXT NOT NULL,
    had_value INTEGER NOT NULL,          -- 操作前该键是否存在
    old_value BLOB,                      -- 操作前的旧值（pickle），had_value=0 时为 NULL
    applied   INTEGER NOT NULL DEFAULT 0,-- 该撤销条目是否已应用（幂等/续滚依据）
    PRIMARY KEY (tx_id, seq)
);
"""


class TransactionalKVStore:
    """支持撤销日志与崩溃恢复的持久化 KV 存储。"""

    def __init__(self, path: str, flush_every: int = 1000):
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._locked: dict[str, int] = {}  # key -> 持有锁的事务 id
        self._flush_every = flush_every
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        self._recover()

    # ---------------- 公共 API ----------------

    def begin(self) -> "Transaction":
        with self._lock:
            cur = self._db.execute("INSERT INTO tx(state) VALUES ('ACTIVE')")
            self._db.commit()
            return Transaction(self, cur.lastrowid)

    def get(self, key: str, default=None):
        """读取键值；若该键被未结束事务锁定则阻塞，直到事务提交或回滚完成。"""
        with self._cond:
            while key in self._locked:
                self._cond.wait()
            row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return default if row is None else pickle.loads(row[0])

    def dump(self) -> dict:
        """导出当前全部数据（用于测试对拍）。"""
        with self._lock:
            return {k: pickle.loads(v) for k, v in self._db.execute("SELECT key, value FROM kv")}

    def tx_state(self, tx_id: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT state FROM tx WHERE id = ?", (tx_id,)).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------- 内部：恢复与回滚 ----------------

    def _recover(self) -> None:
        """打开库时恢复：所有未完结的事务一律回滚到完成状态。"""
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM tx WHERE state IN ('ACTIVE','ROLLING_BACK')")]
            for tx_id in ids:
                self._rollback_tx_locked(tx_id)

    def _rollback_tx_locked(self, tx_id: int, batch_size: int = 1000, after_apply=None) -> None:
        """按逆序应用撤销日志；分批提交，崩溃后可从断点继续。调用方须持有锁。"""
        self._db.execute(
            "UPDATE tx SET state='ROLLING_BACK' WHERE id = ? AND state IN ('ACTIVE','ROLLING_BACK')",
            (tx_id,))
        self._db.commit()
        while True:
            rows = self._db.execute(
                "SELECT seq, key, had_value, old_value FROM undo "
                "WHERE tx_id = ? AND applied = 0 ORDER BY seq DESC LIMIT ?",
                (tx_id, batch_size)).fetchall()
            if not rows:
                break
            with self._db:  # 整批原子提交：应用逆操作 + 标记 applied
                for seq, key, had, old in rows:
                    if had:
                        self._db.execute(
                            "INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, old))
                    else:
                        self._db.execute("DELETE FROM kv WHERE key = ?", (key,))
                    self._db.execute(
                        "UPDATE undo SET applied = 1 WHERE tx_id = ? AND seq = ?", (tx_id, seq))
            if after_apply is not None:  # 测试钩子：在批次持久化之后回调
                for seq, _key, _had, _old in rows:
                    after_apply(tx_id, seq)
        self._db.execute("UPDATE tx SET state='ROLLED_BACK' WHERE id = ?", (tx_id,))
        self._db.commit()

    def _wait_key_locked(self, key: str, tx_id: int) -> None:
        while self._locked.get(key, tx_id) != tx_id:
            self._cond.wait()


class Transaction:
    """单个事务。写操作立即记录撤销日志并锁定涉及的键。"""

    def __init__(self, store: TransactionalKVStore, tx_id: int):
        self._store = store
        self._id = tx_id
        self._seq = 0
        self._done = False
        self._keys: set[str] = set()
        self._undo_pending: list[tuple[int, str, bool, object]] = []
        self._writes: dict[str, object] = {}

    # ---------------- 操作 ----------------

    def set(self, key: str, value) -> None:
        st = self._store
        with st._cond:
            self._check_active()
            st._wait_key_locked(key, self._id)
            had, old = self._view(key)
            self._undo_pending.append((self._seq, key, had, old))
            self._writes[key] = value
            self._after_op(key)

    def delete(self, key: str) -> None:
        st = self._store
        with st._cond:
            self._check_active()
            st._wait_key_locked(key, self._id)
            had, old = self._view(key)
            self._undo_pending.append((self._seq, key, had, old))
            self._writes[key] = _TOMBSTONE
            self._after_op(key)

    def get(self, key: str, default=None):
        """事务内读取：读己之写。"""
        st = self._store
        with st._cond:
            self._check_active()
            st._wait_key_locked(key, self._id)
            had, value = self._view(key)
            return value if had else default

    def commit(self) -> None:
        st = self._store
        with st._cond:
            self._check_active()
            self._flush_locked()
            st._db.execute("UPDATE tx SET state='COMMITTED' WHERE id = ?", (self._id,))
            st._db.commit()
            self._release_locked()
            self._done = True

    def rollback(self, batch_size: int = 1000, after_apply=None) -> None:
        """回滚。幂等：已结束的事务再次调用为空操作。

        若回滚中途抛异常（模拟失败），事务保持 ROLLING_BACK，
        再次调用 rollback() 或重新打开库即可从断点继续。
        """
        st = self._store
        with st._cond:
            if self._done:
                return
            self._flush_locked()
            st._rollback_tx_locked(self._id, batch_size, after_apply)
            self._release_locked()
            self._done = True

    # ---------------- 内部 ----------------

    def _after_op(self, key: str) -> None:
        st = self._store
        self._seq += 1
        st._locked[key] = self._id
        self._keys.add(key)
        if len(self._undo_pending) >= st._flush_every:
            self._flush_locked()

    def _view(self, key: str):
        """事务视角下的当前值：先看本事务未落盘的写，再看库。"""
        if key in self._writes:
            value = self._writes[key]
            return (False, None) if value is _TOMBSTONE else (True, value)
        row = self._store._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return (False, None) if row is None else (True, pickle.loads(row[0]))

    def _flush_locked(self) -> None:
        """把累积的写与撤销日志在同一个 SQLite 事务中原子落盘。"""
        if not self._undo_pending and not self._writes:
            return
        db = self._store._db
        with db:
            for seq, key, had, old in self._undo_pending:
                db.execute(
                    "INSERT INTO undo(tx_id, seq, key, had_value, old_value, applied) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (self._id, seq, key, int(had), pickle.dumps(old) if had else None))
            for key, value in self._writes.items():
                if value is _TOMBSTONE:
                    db.execute("DELETE FROM kv WHERE key = ?", (key,))
                else:
                    db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)",
                               (key, pickle.dumps(value)))
        self._undo_pending.clear()
        self._writes.clear()

    def _release_locked(self) -> None:
        st = self._store
        for key in self._keys:
            st._locked.pop(key, None)
        self._keys.clear()
        st._cond.notify_all()

    def _check_active(self) -> None:
        if self._done:
            raise RuntimeError("transaction already finished")
