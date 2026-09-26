"""
lease_lock —— 基于文件的互斥锁 + 租约续期 + 代际 fencing（仅标准库，POSIX）。

设计要点
--------
* 锁文件（JSON）包含：持有者标识 holder、一次性 token、租约到期时间
  expires_at、代际编号 generation、最近写入时间 renewed_at。
* 获取 / 抢占 / 续期 / 释放的“读-改-写”临界区由一个 guard 文件上的
  flock(LOCK_EX) 保护。flock 由内核在进程退出（包括 SIGKILL）时自动释放，
  因此 guard 永远不会残留；租约文件本身只承担“带过期时间的互斥”。
* 强杀恢复：持有者被 kill -9 后，租约在 expires_at 到期，其他进程可安全
  抢占，代际编号单调递增（generation 同时持久化在 sidecar 文件中，
  即使锁文件损坏也能延续代际）。
* 旧持有者防护：续期 / 释放前都会校验 token + generation，不匹配即抛
  PreemptedError 并主动放弃；资源侧提供 FencedResource，拒绝低于已见
  最高代际的写入。
* 时钟漂移：续期时交叉校验 wall clock 与 monotonic clock、锁文件内
  renewed_at、锁文件 mtime，发现回拨 / 跳变即抛 ClockDriftError。
"""

from __future__ import annotations

import json
import os
import fcntl
import threading
import time
import uuid

__all__ = [
    "LeaseLock",
    "FencedResource",
    "LockError",
    "AcquireTimeoutError",
    "LockLostError",
    "PreemptedError",
    "ClockDriftError",
    "StaleGenerationError",
]


# ---------------------------------------------------------------- 异常体系

class LockError(Exception):
    """锁相关错误基类。"""


class AcquireTimeoutError(LockError):
    """在指定超时时间内未能获取锁。"""


class LockLostError(LockError):
    """持有期间锁已丢失（被删 / 被改 / 被抢占 / 时钟异常）。收到此错误必须停止操作。"""


class PreemptedError(LockLostError):
    """锁已被其他进程抢占（代际已前进）。"""


class ClockDriftError(LockLostError):
    """检测到时钟回拨 / 跳变 / 文件时间戳来自未来。"""


class StaleGenerationError(LockError):
    """资源侧 fencing：写入携带的代际编号已过期。"""


# ---------------------------------------------------------------- 内部工具

def _default_holder_id() -> str:
    return f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class _Guard:
    """guard 文件上的 flock 临界区；进程死亡时由内核自动释放。"""

    def __init__(self, path: str):
        self._fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)

    def __enter__(self):
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
        return False


def _atomic_write_text(path: str, text: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path: str, obj: dict) -> None:
    """同目录临时文件 + fsync + os.replace，保证读者只看到完整内容。"""
    tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# ---------------------------------------------------------------- 主类

class LeaseLock:
    """基于文件的租约互斥锁。一个实例代表一个持有者。"""

    def __init__(
        self,
        path: str,
        holder_id: str | None = None,
        lease_duration: float = 5.0,
        drift_tolerance: float = 0.5,
        clock=None,
        monotonic=None,
    ):
        if lease_duration <= 0:
            raise ValueError("lease_duration 必须为正数")
        self.path = path
        self.holder_id = holder_id or _default_holder_id()
        self.lease_duration = float(lease_duration)
        self.drift_tolerance = float(drift_tolerance)
        # clock/monotonic 可注入，便于时钟漂移测试；默认真实时钟。
        self._clock = clock or time.time
        self._monotonic = monotonic or time.monotonic
        self._real_clock = clock is None

        self.token: str | None = None
        self.generation: int | None = None
        self._last_wall: float | None = None
        self._last_mono: float | None = None

        self._renew_thread: threading.Thread | None = None
        self._renew_stop = threading.Event()

    # ------------------------------------------------------------ 属性

    @property
    def is_held(self) -> bool:
        return self.token is not None

    def _guard_path(self) -> str:
        return self.path + ".guard"

    def _gen_path(self) -> str:
        return self.path + ".gen"

    # ------------------------------------------------------------ 记录读写

    def _read_record(self):
        """返回 dict / "missing" / "corrupt"。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return "missing"
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError):
            return "corrupt"
        if not isinstance(data, dict):
            return "corrupt"
        required = ("holder", "token", "generation", "expires_at", "renewed_at")
        if any(k not in data for k in required):
            return "corrupt"
        return data

    def _read_sidecar_gen(self) -> int:
        try:
            with open(self._gen_path(), "r", encoding="utf-8") as f:
                return max(0, int(f.read().strip()))
        except (OSError, ValueError):
            return 0

    def _write_sidecar_gen(self, gen: int) -> None:
        _atomic_write_text(self._gen_path(), str(gen))

    # ------------------------------------------------------------ 获取 / 抢占

    def acquire(self, timeout: float | None = None, retry_interval: float = 0.02) -> "LeaseLock":
        """阻塞直到获取锁；超时抛 AcquireTimeoutError。"""
        deadline = None if timeout is None else self._monotonic() + timeout
        while True:
            if self.try_acquire():
                return self
            if deadline is not None and self._monotonic() >= deadline:
                raise AcquireTimeoutError(
                    f"在 {timeout}s 内未能获取锁 {self.path}"
                )
            remaining = None if deadline is None else max(0.0, deadline - self._monotonic())
            time.sleep(min(retry_interval, remaining) if remaining is not None else retry_interval)

    def try_acquire(self) -> bool:
        """尝试获取一次（含对过期 / 损坏锁的抢占）。成功返回 True。"""
        if self.is_held:
            raise LockError("当前实例已持有锁，请先 release")
        now = self._clock()
        token = uuid.uuid4().hex
        with _Guard(self._guard_path()):
            rec = self._read_record()
            if isinstance(rec, dict):
                if rec["expires_at"] > now:
                    return False  # 锁仍有效
                gen = int(rec["generation"]) + 1  # 过期抢占：代际 +1
            elif rec == "missing":
                gen = self._read_sidecar_gen() + 1
            else:  # corrupt：保守等待一个完整租约周期（按文件 mtime）后才允许抢占
                try:
                    mtime = os.stat(self.path).st_mtime
                except FileNotFoundError:
                    mtime = 0.0
                if self._real_clock and mtime + self.lease_duration > time.time():
                    return False
                gen = self._read_sidecar_gen() + 1

            record = {
                "holder": self.holder_id,
                "token": token,
                "generation": gen,
                "lease_duration": self.lease_duration,
                "renewed_at": now,
                "expires_at": now + self.lease_duration,
            }
            _atomic_write_json(self.path, record)
            self._write_sidecar_gen(gen)

        self.token = token
        self.generation = gen
        self._last_wall = now
        self._last_mono = self._monotonic()
        return True

    # ------------------------------------------------------------ 续期

    def renew(self) -> float:
        """续期，返回新的 expires_at。任何异常都意味着必须放弃操作。"""
        if not self.is_held:
            raise LockLostError("未持有锁，无法续期")
        now = self._clock()
        mono = self._monotonic()
        try:
            self._check_local_drift(now, mono)
        except ClockDriftError:
            self._abandon()
            raise

        with _Guard(self._guard_path()):
            rec = self._read_record()
            if rec == "missing":
                self._abandon()
                raise LockLostError("续期失败：锁文件已被删除")
            if rec == "corrupt":
                self._abandon()
                raise LockLostError("续期失败：锁文件内容损坏或被篡改")
            if rec["token"] != self.token or rec["generation"] != self.generation:
                self._abandon()
                raise PreemptedError(
                    f"锁已被抢占：当前持有者为 {rec['holder']!r}，代际 {rec['generation']}"
                )
            if now < rec["renewed_at"] - self.drift_tolerance:
                self._abandon()
                raise ClockDriftError(
                    f"检测到时钟回拨：now={now:.3f} < renewed_at={rec['renewed_at']:.3f}"
                )
            if self._real_clock:
                mtime = os.stat(self.path).st_mtime
                if mtime > now + self.drift_tolerance:
                    self._abandon()
                    raise ClockDriftError(
                        f"锁文件修改时间来自未来：mtime={mtime:.3f} > now={now:.3f}"
                    )

            rec["renewed_at"] = now
            rec["expires_at"] = now + self.lease_duration
            _atomic_write_json(self.path, rec)

            # 写后回读校验：防止临界区内被并发篡改后静默成功。
            back = self._read_record()
            if (
                not isinstance(back, dict)
                or back["token"] != self.token
                or back["generation"] != self.generation
            ):
                self._abandon()
                raise LockLostError("续期后回读校验失败：锁文件被并发修改")

        self._last_wall = now
        self._last_mono = mono
        return rec["expires_at"]

    def _check_local_drift(self, now: float, mono: float) -> None:
        if self._last_wall is None:
            return
        wall_delta = now - self._last_wall
        mono_delta = mono - self._last_mono
        if wall_delta < -self.drift_tolerance:
            raise ClockDriftError(
                f"检测到时钟回拨：wall 倒退 {-wall_delta:.3f}s"
            )
        if abs(wall_delta - mono_delta) > max(self.drift_tolerance, 1.0):
            raise ClockDriftError(
                f"检测到时钟跳变：wall 前进 {wall_delta:.3f}s，monotonic 前进 {mono_delta:.3f}s"
            )

    # ------------------------------------------------------------ 释放 / 校验

    def release(self) -> None:
        """释放锁。若锁已不属于自己，抛 LockLostError（本地状态同样会被清除）。"""
        if not self.is_held:
            return
        try:
            with _Guard(self._guard_path()):
                rec = self._read_record()
                if (
                    isinstance(rec, dict)
                    and rec["token"] == self.token
                    and rec["generation"] == self.generation
                ):
                    try:
                        os.unlink(self.path)
                    except FileNotFoundError:
                        pass
                else:
                    raise LockLostError("释放时发现锁已丢失或被抢占")
        finally:
            self._abandon()

    def validate(self) -> bool:
        """校验自己是否仍是合法持有者（token + generation 匹配且未过期）。"""
        if not self.is_held:
            return False
        with _Guard(self._guard_path()):
            rec = self._read_record()
        return (
            isinstance(rec, dict)
            and rec["token"] == self.token
            and rec["generation"] == self.generation
            and rec["expires_at"] > self._clock()
        )

    def _abandon(self) -> None:
        """主动放弃：清空本地持有状态，绝不静默继续。"""
        self.token = None
        self.generation = None
        self._renew_stop.set()

    # ------------------------------------------------------------ 自动续期

    def start_auto_renew(self, interval: float | None = None, on_lost=None) -> None:
        """后台线程自动续期；续期失败时回调 on_lost(exc) 并停止。"""
        if not self.is_held:
            raise LockLostError("未持有锁，无法启动自动续期")
        interval = interval or self.lease_duration / 3.0
        self._renew_stop.clear()

        def _run():
            while not self._renew_stop.wait(interval):
                try:
                    self.renew()
                except LockLostError as exc:
                    if on_lost is not None:
                        on_lost(exc)
                    return

        self._renew_thread = threading.Thread(
            target=_run, name=f"lease-renew-{self.holder_id}", daemon=True
        )
        self._renew_thread.start()

    def stop_auto_renew(self) -> None:
        self._renew_stop.set()
        if self._renew_thread is not None:
            self._renew_thread.join(timeout=2.0)
            self._renew_thread = None


# ---------------------------------------------------------------- 资源侧 fencing

class FencedResource:
    """带代际 fencing 的资源：拒绝低于已见最高代际的写入。

    即使锁层出现极端异常（如代际 sidecar 同时损坏），资源侧持久化的
    最高代际仍是最后一道防线，保证旧持有者无法继续写入。
    """

    def __init__(self, path: str):
        self.path = path
        self.fencing_path = path + ".fencing"

    def _highest_generation(self) -> int:
        try:
            with open(self.fencing_path, "r", encoding="utf-8") as f:
                return max(0, int(f.read().strip()))
        except (OSError, ValueError):
            return 0

    def write(self, lock: LeaseLock, data) -> None:
        """以 lock 的代际编号写入数据；代际过期抛 StaleGenerationError。"""
        if lock.generation is None:
            raise LockLostError("锁未持有，禁止写入")
        gen = lock.generation
        with _Guard(self.path + ".guard"):
            highest = self._highest_generation()
            if gen <= highest:
                raise StaleGenerationError(
                    f"代际 {gen} 已被代际 {highest} 超越，拒绝写入（持有者 {lock.holder_id!r}）"
                )
            payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
            with open(self.path, "ab") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            _atomic_write_text(self.fencing_path, str(gen))

    def read(self) -> bytes:
        try:
            with open(self.path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return b""
