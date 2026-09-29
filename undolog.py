"""undolog: 事务撤销日志与回滚库（仅标准库）。

模型
----
- 单写入者：同一时刻只允许一个活跃事务（写-写串行化）；读并发安全。
- 每个写操作在应用前先把逆操作（旧值 / 是否存在）追加到磁盘撤销日志。
- 回滚按逆序应用逆操作；每步记录 undone 标记，天然幂等。
- 回滚中途失败 / 进程崩溃后，重新打开库时 recover 会从断点继续回滚到完成。
- 回滚期间，受影响 key 被隔离（quarantine）：get 阻塞、try_get 拒绝，
  读者永远不会读到回滚中间状态。

日志记录（JSON Lines，仅追加）：
  begin / op / commit / rollback_begin / undone / rollback_done
崩溃时尾部可能出现半行，recover 会截断容忍。
"""

from __future__ import annotations

import json
import os
import threading


class TxError(Exception):
    """事务状态错误（如重复提交、提交后仍写入等）。"""


class KeyQuarantined(TxError):
    """try_get/try_set 命中正在回滚的 key 时抛出。"""


class TxStore:
    """带撤销日志的键值存储。value 须为 JSON 可序列化对象，key 为 str。"""

    def __init__(self, path: str, durable: bool = True):
        self._path = path
        self._durable = durable
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._data: dict = {}
        self._quarantined: set = set()
        self._active: Transaction | None = None
        self._next_tx = 1
        self._last_tx = 0
        self._undo_hook = None  # 测试注入点：每个 undo 步前调用 hook(count)
        self._log = open(path, "a+b")
        self._pending_flush = False
        self._recover()

    # ---------------- 日志 ----------------

    def _append(self, rec: dict, flush: bool = False) -> None:
        self._log.write(json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n")
        if flush:
            self._flush()
        else:
            self._pending_flush = True

    def _flush(self) -> None:
        self._log.flush()
        if self._durable:
            os.fsync(self._log.fileno())
        self._pending_flush = False

    def _maybe_flush(self) -> None:
        if self._pending_flush:
            self._flush()

    # ---------------- 崩溃恢复 / 断点续滚 ----------------

    def _recover(self) -> None:
        self._log.seek(0)
        raw = self._log.read()
        self._log.seek(0, os.SEEK_END)
        txs: dict[int, dict] = {}
        order: list[int] = []
        for line in raw.split(b"\n"):
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                break  # 崩溃造成的撕裂写，丢弃尾部
            kind = rec["type"]
            txid = rec.get("tx")
            if kind == "begin":
                txs[txid] = {"ops": [], "committed": False,
                             "rolling": False, "done": False, "undone": set()}
                order.append(txid)
            elif kind == "op":
                txs[txid]["ops"].append(rec)
            elif kind == "commit":
                txs[txid]["committed"] = True
            elif kind == "rollback_begin":
                txs[txid]["rolling"] = True
            elif kind == "undone":
                txs[txid]["undone"].add(rec["seq"])
            elif kind == "rollback_done":
                txs[txid]["done"] = True
        if order:
            self._next_tx = order[-1] + 1
            self._last_tx = order[-1]
        # 按日志顺序重放，构造一致状态
        for txid in order:
            st = txs[txid]
            for op in st["ops"]:
                if op.get("del"):
                    self._data.pop(op["key"], None)
                else:
                    self._data[op["key"]] = op["new"]
            if not st["committed"] or st["rolling"]:
                # 未提交（崩溃于事务中途）或处于回滚中：逆序撤销全部操作。
                # 应用旧值本身幂等，重复执行结果一致。
                for op in reversed(st["ops"]):
                    if op["had"]:
                        self._data[op["key"]] = op["old"]
                    else:
                        self._data.pop(op["key"], None)
                if st["rolling"] and not st["done"]:
                    # 断点续滚：补齐缺失的 undone 标记并写完成标记
                    for op in reversed(st["ops"]):
                        if op["seq"] not in st["undone"]:
                            self._append({"type": "undone", "tx": txid,
                                          "seq": op["seq"]})
                    self._append({"type": "rollback_done", "tx": txid},
                                 flush=True)
        self._maybe_flush()

    # ---------------- 事务接口 ----------------

    def begin(self) -> "Transaction":
        with self._lock:
            if self._active is not None:
                raise TxError("another transaction is active (single-writer)")
            txid = self._next_tx
            self._next_tx += 1
            self._last_tx = txid
            self._append({"type": "begin", "tx": txid})
            tx = Transaction(self, txid)
            self._active = tx
            return tx

    # ---------------- 读写接口 ----------------

    def get(self, key, default=None):
        """阻塞读：若 key 正在回滚则等待回滚完成，绝不返回中间状态。"""
        with self._cond:
            while key in self._quarantined:
                self._cond.wait()
            return self._data.get(key, default)

    def try_get(self, key, default=None):
        """快速失败读：key 正在回滚时抛 KeyQuarantined。"""
        with self._lock:
            if key in self._quarantined:
                raise KeyQuarantined(key)
            return self._data.get(key, default)

    def dump(self) -> dict:
        """返回当前数据快照（等待回滚完成后的一致视图）。"""
        with self._cond:
            while self._quarantined:
                self._cond.wait()
            return dict(self._data)

    def resume(self) -> None:
        """进程内断点续滚：继续完成被异常打断的回滚。幂等。"""
        tx = self._active
        if tx is not None and tx._state == "rolling":
            tx.rollback()

    def close(self) -> None:
        with self._lock:
            self._maybe_flush()
            self._log.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Transaction:
    def __init__(self, store: TxStore, txid: int):
        self._store = store
        self._id = txid
        self._seq = 0
        self._ops: list[dict] = []
        self._keys: set = set()
        self._undone: set[int] = set()
        self._state = "active"  # active -> committed | rolling -> rolled_back

    @property
    def state(self) -> str:
        return self._state

    def set(self, key: str, value) -> None:
        store = self._store
        with store._lock:
            if self._state != "active":
                raise TxError(f"cannot set in state {self._state}")
            had = key in store._data
            old = store._data.get(key)
            rec = {"type": "op", "tx": self._id, "seq": self._seq,
                   "key": key, "had": had, "old": old, "new": value}
            store._append(rec)
            store._data[key] = value
            self._ops.append(rec)
            self._keys.add(key)
            self._seq += 1

    def delete(self, key: str) -> None:
        """删除 key（不存在则为 no-op）。逆操作仍是恢复 had/old，可回滚。"""
        store = self._store
        with store._lock:
            if self._state != "active":
                raise TxError(f"cannot delete in state {self._state}")
            had = key in store._data
            old = store._data.get(key)
            rec = {"type": "op", "tx": self._id, "seq": self._seq,
                   "key": key, "had": had, "old": old, "del": True}
            store._append(rec)
            store._data.pop(key, None)
            self._ops.append(rec)
            self._keys.add(key)
            self._seq += 1

    def commit(self) -> None:
        store = self._store
        with store._lock:
            if self._state != "active":
                raise TxError(f"cannot commit in state {self._state}")
            store._append({"type": "commit", "tx": self._id}, flush=True)
            self._state = "committed"
            store._active = None

    def rollback(self) -> None:
        """逆序回滚。幂等：重复调用结果一致；中断后再次调用从断点继续。"""
        store = self._store
        with store._lock:
            if self._state == "rolled_back":
                return
            if self._state == "committed" and self._id != store._last_tx:
                raise TxError("only the latest transaction can be rolled back")
            if self._state != "rolling":
                store._append({"type": "rollback_begin", "tx": self._id,
                               "total": self._seq}, flush=True)
            self._state = "rolling"
            store._active = self  # 回滚完成前不允许开启新事务
            store._quarantined.update(self._keys)
        count = 0
        try:
            for op in reversed(self._ops):
                if op["seq"] in self._undone:
                    continue  # 断点续滚：跳过已完成步骤
                hook = store._undo_hook
                if hook is not None:
                    hook(count)
                count += 1
                with store._lock:
                    if op["had"]:
                        store._data[op["key"]] = op["old"]
                    else:
                        store._data.pop(op["key"], None)
                    store._append({"type": "undone", "tx": self._id,
                                   "seq": op["seq"]})
                    self._undone.add(op["seq"])
            with store._cond:
                store._append({"type": "rollback_done", "tx": self._id},
                              flush=True)
                self._state = "rolled_back"
                store._active = None
                store._quarantined.difference_update(self._keys)
                store._cond.notify_all()
        except BaseException:
            # 回滚被打断：保持 quarantine，读者仍被隔离；
            # 日志中已有 rollback_begin + 部分 undone，可续滚。
            store._maybe_flush()
            raise
