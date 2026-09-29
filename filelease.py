"""filelease: 基于文件的互斥锁 + 租约续期 + 代际 fencing（仅标准库）。

设计要点
--------
* 锁状态文件 `<path>`（JSON）：holder / generation / issued_at / ttl / expires_at / state。
* 守卫文件 `<path>.guard` 上的 flock 用于串行化「读-改-写」。
  flock 由内核在进程死亡（含 kill -9）时自动释放，因此守卫永远不会卡死；
  真正的活性由租约到期时间保证。
* 代际编号（fencing token）单调递增，持久化在租约文件与 `<path>.gen` 旁车文件中；
  旧持有者被抢占后，其续期/释放/受保护写入都会被拒绝。
* 时钟漂移检测：续期时对比「墙钟锚点 + 单调钟增量」与当前墙钟，
  偏差超过 drift_tolerance 即判定漂移（含时钟回拨），持有者必须放弃。
* 可观测性（可选，默认关闭）：report=True 时把获取/续期/释放/抢占/损坏隔离
  等事件以 JSONL 追加到 `<path>.events`。事件与锁文件状态变更在同一把
  flock 守卫内写入，且每个状态事件内嵌当时落盘的 record 快照，
  因此报告数据可与锁文件逐条对数。开启报告不改变加解锁语义与租约时长。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import tempfile
import os
import socket
import threading
import time
import uuid


# ---------------------------------------------------------------- 异常层级

class LockError(Exception):
    """本库所有异常的基类。"""


class AcquireTimeout(LockError):
    """在指定超时内未能获取锁。"""


class LeaseLost(LockError):
    """租约已丢失：文件被删 / 内容损坏 / 时钟漂移等。持有者必须放弃。"""


class LeaseStolen(LeaseLost):
    """租约已被他人抢占（代际或持有者不匹配）。"""


class LeaseExpired(LeaseLost):
    """续期时租约已经过期（持有者自己拖延太久）。"""


class ClockDriftDetected(LeaseLost):
    """检测到时钟漂移（如墙钟回拨），租约时间不再可信。"""


class _Corrupt(LockError):
    """内部使用：租约文件内容无法解析。"""


def _default_holder_id() -> str:
    return "%s:%d:%s" % (socket.gethostname(), os.getpid(), uuid.uuid4().hex[:8])


def _fsync_dir(dirpath: str) -> None:
    fd = os.open(dirpath, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- 锁本体

class FileLeaseLock:
    """一个由文件承载的互斥锁，带租约与代际编号。

    参数:
        path:             锁文件路径。
        holder_id:        持有者标识（默认 主机:pid:随机）。
        ttl:              租约时长（秒）。
        drift_tolerance:  时钟漂移容忍（秒）。
        time_fn/mono_fn:  可注入的墙钟 / 单调钟（测试时钟回拨用）。
    """

    def __init__(self, path, holder_id=None, ttl=10.0, drift_tolerance=0.5,
                 time_fn=time.time, mono_fn=time.monotonic,
                 report=False, report_path=None):
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.path = os.fspath(path)
        self.guard_path = self.path + ".guard"
        self.gen_path = self.path + ".gen"
        self.report_path = (os.fspath(report_path) if report_path
                            else self.path + ".events") if report else None
        self.holder_id = holder_id or _default_holder_id()
        self.ttl = float(ttl)
        self.drift_tolerance = float(drift_tolerance)
        self._time = time_fn
        self._mono = mono_fn

    # ---------------- 事件日志（可观测性） ----------------

    def _log_event(self, event: dict) -> None:
        """把一条事件追加到 <path>.events（JSONL + fsync）。

        必须在 _guard 临界区内、且紧跟对应的锁文件写之后调用，
        这样事件流与锁文件状态转移一一对应，可对数。
        事件写失败会向上抛出（fail-closed），保证「日志与锁文件一致」
        不被静默破坏；report 关闭时本函数完全不会被调用。
        """
        if self.report_path is None:
            return
        line = json.dumps(event, sort_keys=True).encode("utf-8") + b"\n"
        fd = os.open(self.report_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    # ---------------- 底层 IO ----------------

    @contextlib.contextmanager
    def _guard(self):
        """flock 临界区：串行化租约文件的读-改-写。进程死亡时内核自动释放。"""
        fd = os.open(self.guard_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read_record(self):
        """返回租约记录 dict；文件不存在返回 None；内容损坏抛 _Corrupt。"""
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return None
        try:
            rec = json.loads(raw.decode("utf-8"))
            for key in ("state", "holder", "generation", "issued_at", "ttl", "expires_at"):
                rec[key]
            if not isinstance(rec["generation"], int):
                raise KeyError("generation")
            return rec
        except Exception as exc:
            raise _Corrupt("unparseable lock file: %r" % (exc,))

    def _atomic_write(self, path, payload: bytes) -> None:
        dirpath = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".tmp-", suffix=".lease")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)          # 原子替换
            _fsync_dir(dirpath)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    def _write_record(self, rec: dict) -> None:
        self._atomic_write(self.path, json.dumps(rec, sort_keys=True).encode("utf-8"))

    def _read_sidecar_gen(self) -> int:
        try:
            with open(self.gen_path, "r", encoding="ascii") as fh:
                return int(fh.read().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def _write_sidecar_gen(self, generation: int) -> None:
        self._atomic_write(self.gen_path, ("%d\n" % generation).encode("ascii"))

    def _quarantine_corrupt(self) -> None:
        """把损坏的锁文件挪走，避免反复解析失败；保留现场便于排查。"""
        tomb = "%s.corrupt-%d-%s" % (self.path, int(self._time()), uuid.uuid4().hex[:6])
        with contextlib.suppress(FileNotFoundError):
            os.replace(self.path, tomb)

    # ---------------- 获取 ----------------

    def try_acquire(self, waited=0.0):
        """非阻塞获取；成功返回 Lease，锁被他人持有且未过期返回 None。

        waited: 调用方已为本次获取等待的秒数（仅用于事件日志）。
        """
        with self._guard():
            now = self._time()
            sidecar_gen = self._read_sidecar_gen()
            try:
                rec = self._read_record()
            except _Corrupt:
                # 损坏文件不可信：隔离后按缺失处理，代际从旁车文件恢复。
                self._quarantine_corrupt()
                self._log_event({"v": 1, "ts": now, "event": "corrupt_quarantine",
                                 "holder": self.holder_id})
                rec = None

            if rec is None:
                base_gen = sidecar_gen
                preempted = None
            elif rec["state"] != "held" or rec["expires_at"] <= now:
                base_gen = max(rec["generation"], sidecar_gen)   # 已释放或已过期：允许抢占
                preempted = None
                if rec["state"] == "held":
                    # 上一持有者未释放就已过期：异常退出（或停滞）后的回收。
                    preempted = {
                        "holder": rec["holder"],
                        "generation": rec["generation"],
                        "expired_at": rec["expires_at"],
                        "reclaim_delay_ms": round((now - rec["expires_at"]) * 1000, 3),
                    }
            else:
                return None                                       # 他人持有且未过期

            generation = base_gen + 1
            new_rec = {
                "version": 1,
                "state": "held",
                "holder": self.holder_id,
                "generation": generation,
                "issued_at": now,
                "ttl": self.ttl,
                "expires_at": now + self.ttl,
            }
            self._write_record(new_rec)
            self._write_sidecar_gen(generation)
            self._log_event({
                "v": 1, "ts": now, "event": "acquire",
                "holder": self.holder_id,
                "generation": generation,
                "prev_generation": rec["generation"] if rec else None,
                "wait_ms": round(waited * 1000, 3),
                "preempted": preempted,
                "record": dict(new_rec),
            })
            return Lease(self, generation, new_rec["expires_at"])

    def acquire(self, timeout=None, poll_interval=0.05):
        """阻塞获取，直到成功或超时（timeout=None 表示无限等待）。"""
        start = self._mono()
        deadline = None if timeout is None else self._mono() + timeout
        while True:
            lease = self.try_acquire(waited=self._mono() - start)
            if lease is not None:
                return lease
            if deadline is not None and self._mono() >= deadline:
                with self._guard():
                    self._log_event({"v": 1, "ts": self._time(),
                                     "event": "acquire_timeout",
                                     "holder": self.holder_id,
                                     "wait_ms": round((self._mono() - start) * 1000, 3)})
                raise AcquireTimeout("could not acquire %s within %.3fs"
                                     % (self.path, timeout))
            self._mono_sleep(min(poll_interval, 0.05))

    @staticmethod
    def _mono_sleep(seconds):
        time.sleep(seconds)


# ---------------------------------------------------------------- 租约句柄

class Lease:
    """一次成功获取的句柄。所有后续操作都带代际校验。"""

    def __init__(self, lock: FileLeaseLock, generation: int, expires_at: float):
        self._lock = lock
        self.holder_id = lock.holder_id
        self.generation = generation
        self.expires_at = expires_at
        self._wall_anchor = lock._time()
        self._mono_anchor = lock._mono()
        self._valid = True

    # fencing token：下游资源用它拒绝旧持有者的写入
    @property
    def fencing_token(self) -> int:
        return self.generation

    def _check_clock(self, now: float) -> None:
        lock = self._lock
        expected_wall = self._wall_anchor + (lock._mono() - self._mono_anchor)
        if abs(now - expected_wall) > lock.drift_tolerance:
            raise ClockDriftDetected(
                "wall clock drifted %.3fs (tolerance %.3fs); abandoning lease"
                % (now - expected_wall, lock.drift_tolerance))

    def renew(self) -> float:
        """续期。任何异常都意味着租约不再可信，调用方必须停止操作。

        返回新的到期时间。可能抛出：
        LeaseLost（文件被删/损坏）、LeaseStolen（被抢占）、
        LeaseExpired（已过期）、ClockDriftDetected（时钟漂移）。
        """
        lock = self._lock
        with lock._guard():
            try:
                rec = lock._read_record()
            except _Corrupt:
                self._valid = False
                raise LeaseLost("lock file corrupted; abandoning lease")
            if rec is None:
                self._valid = False
                raise LeaseLost("lock file deleted; abandoning lease")
            if rec["state"] != "held" or rec["holder"] != self.holder_id \
                    or rec["generation"] != self.generation:
                self._valid = False
                raise LeaseStolen(
                    "lease preempted: file now held by %r (gen %s), we are %r (gen %d)"
                    % (rec.get("holder"), rec.get("generation"),
                       self.holder_id, self.generation))

            now = lock._time()
            self._check_clock(now)
            if now > rec["expires_at"]:
                self._valid = False
                raise LeaseExpired("lease already expired before renew")

            prev_expires_at = rec["expires_at"]
            rec["issued_at"] = now
            rec["expires_at"] = now + lock.ttl
            lock._write_record(rec)
            lock._log_event({
                "v": 1, "ts": now, "event": "renew",
                "holder": self.holder_id,
                "generation": self.generation,
                "remaining_ms": round((prev_expires_at - now) * 1000, 3),
                "record": dict(rec),
            })
            self.expires_at = rec["expires_at"]
            self._wall_anchor = now
            self._mono_anchor = lock._mono()
            return self.expires_at

    def release(self) -> None:
        """释放锁。若租约已被抢占/丢失则抛 LeaseLost（不静默）。"""
        lock = self._lock
        with lock._guard():
            try:
                rec = lock._read_record()
            except _Corrupt:
                self._valid = False
                raise LeaseLost("lock file corrupted during release")
            if rec is None or rec["state"] != "held" \
                    or rec["holder"] != self.holder_id \
                    or rec["generation"] != self.generation:
                self._valid = False
                raise LeaseStolen("cannot release: lease no longer ours")
            now = lock._time()
            rec["state"] = "released"
            rec["expires_at"] = now
            lock._write_record(rec)
            lock._log_event({
                "v": 1, "ts": now, "event": "release",
                "holder": self.holder_id,
                "generation": self.generation,
                "since_issued_ms": round((now - rec["issued_at"]) * 1000, 3),
                "record": dict(rec),
            })
            self._valid = False

    def ensure_valid(self) -> None:
        """在执行受保护操作前自检；租约不可信时抛 LeaseLost。"""
        if not self._valid:
            raise LeaseLost("lease handle already invalidated")
        lock = self._lock
        with lock._guard():
            try:
                rec = lock._read_record()
            except _Corrupt:
                rec = None
            now = lock._time()
            self._check_clock(now)
            ok = rec is not None and rec["state"] == "held" \
                and rec["holder"] == self.holder_id \
                and rec["generation"] == self.generation \
                and now <= rec["expires_at"]
        if not ok:
            self._valid = False
            raise LeaseLost("lease no longer valid")

    # ---------------- 自动续期 ----------------

    def auto_renew(self, interval=None, on_lost=None):
        """后台线程自动续期；失败时置 lost_event 并回调 on_lost(exc)。

        返回 (stop_event, lost_event, thread)。调用方应监听 lost_event，
        一旦置位必须立即停止受保护操作——绝不静默继续。
        """
        lock = self._lock
        interval = interval if interval is not None else lock.ttl / 3.0
        stop_event = threading.Event()
        lost_event = threading.Event()

        def _loop():
            while not stop_event.wait(interval):
                try:
                    self.renew()
                except LeaseLost as exc:
                    lost_event.set()
                    if on_lost is not None:
                        on_lost(exc)
                    return

        thread = threading.Thread(target=_loop, name="filelease-renew-%d"
                                  % self.generation, daemon=True)
        thread.start()
        return stop_event, lost_event, thread

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._valid:
            try:
                self.release()
            except LeaseLost:
                pass
        return False

    def __repr__(self):
        return "<Lease holder=%r gen=%d expires_at=%.3f>" % (
            self.holder_id, self.generation, self.expires_at)
