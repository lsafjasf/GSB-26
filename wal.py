"""Write-ahead log (WAL) with checkpoints and segment rotation. Stdlib only.

Semantics
---------
- Every mutation is appended to the current log segment (and fsynced by
  default) BEFORE the in-memory state is updated.
- A checkpoint snapshots the in-memory state and records the log position
  (lsn + segment id) up to which the snapshot is complete. On recovery only
  records with lsn > checkpoint.lsn are replayed.
- The log is split into segments (`NNNN.seg`). Segments fully covered by a
  checkpoint are deleted. The checkpoint file is written atomically
  (tmp file + fsync + rename + dir fsync).
- Replay is idempotent: a record is applied only if its lsn is greater than
  the lsn already applied, so replaying the same log twice is a no-op.
- A torn/corrupt tail of the last segment (e.g. after a crash mid-write) is
  truncated to the last valid record; corruption in a non-last segment is a
  hard error (manual repair required).

Fault injection: pass ``fault_hook(point, **ctx)`` to ``KVStore.open``. It is
invoked at the points "log_write" (before os.write), "log_written" (after
os.write, before fsync), "rotate", "checkpoint_tmp_written" and
"checkpoint_committed". Tests use it to SIGKILL the process mid-operation.
"""

import json
import os
import threading
import zlib

CHECKPOINT_FILE = "checkpoint.json"
CHECKPOINT_TMP = "checkpoint.tmp"
SEG_SUFFIX = ".seg"

__all__ = ["KVStore", "CorruptLogError"]


class CorruptLogError(Exception):
    pass


def _encode_record(lsn, key, value):
    body = json.dumps({"lsn": lsn, "key": key, "value": value},
                      separators=(",", ":"), sort_keys=True)
    crc = zlib.crc32(body.encode("utf-8"))
    line = json.dumps({"crc": crc, "body": body}, separators=(",", ":"))
    return (line + "\n").encode("utf-8")


def _decode_record(line):
    outer = json.loads(line.decode("utf-8"))
    body = outer["body"]
    if not isinstance(body, str) or zlib.crc32(body.encode("utf-8")) != outer["crc"]:
        raise CorruptLogError("record crc mismatch")
    rec = json.loads(body)
    return rec["lsn"], rec["key"], rec["value"]


def _encode_checkpoint(lsn, segment, state):
    body = json.dumps({"lsn": lsn, "segment": segment, "state": state},
                      separators=(",", ":"), sort_keys=True)
    crc = zlib.crc32(body.encode("utf-8"))
    return json.dumps({"crc": crc, "body": body}, separators=(",", ":")).encode("utf-8")


def _decode_checkpoint(data):
    outer = json.loads(data.decode("utf-8"))
    body = outer["body"]
    if not isinstance(body, str) or zlib.crc32(body.encode("utf-8")) != outer["crc"]:
        raise CorruptLogError("checkpoint crc mismatch")
    cp = json.loads(body)
    return cp["lsn"], cp["segment"], cp["state"]


class KVStore:
    """A tiny WAL-backed key-value store. Keys must be str, values must be
    JSON-serializable."""

    def __init__(self, directory, max_segment_bytes=4 * 1024 * 1024,
                 fsync=True, fault_hook=None):
        self._dir = directory
        self._max_seg = max_segment_bytes
        self._fsync = fsync
        self._fault = fault_hook or (lambda point, **ctx: None)
        self._lock = threading.RLock()
        self._state = {}
        self._applied_lsn = 0
        self._seg_id = 0
        self._fd = -1
        self._seg_size = 0

    # ------------------------------------------------------------------ open

    @classmethod
    def open(cls, directory, **kw):
        """Open (or create) a store, recovering from checkpoint + log."""
        self = cls(directory, **kw)
        os.makedirs(directory, exist_ok=True)

        cp_lsn, cp_seg, cp_state = 0, 0, None
        cp_path = os.path.join(directory, CHECKPOINT_FILE)
        if os.path.exists(cp_path):
            with open(cp_path, "rb") as f:
                cp_lsn, cp_seg, cp_state = _decode_checkpoint(f.read())
        self._state = dict(cp_state or {})
        self._applied_lsn = cp_lsn

        seg_ids = sorted(
            int(name[: -len(SEG_SUFFIX)])
            for name in os.listdir(directory)
            if name.endswith(SEG_SUFFIX)
        )
        last_seg = seg_ids[-1] if seg_ids else None

        # Replay log records newer than the checkpoint.
        for sid in seg_ids:
            if sid < cp_seg:
                continue  # fully covered by the checkpoint
            path = self._seg_path(sid)
            valid_end = 0
            corrupt = False
            with open(path, "rb") as f:
                while True:
                    line = f.readline()
                    if not line:
                        break
                    if not line.strip():
                        valid_end += len(line)
                        continue
                    try:
                        lsn, key, value = _decode_record(line)
                    except Exception:
                        corrupt = True
                        break
                    valid_end += len(line)
                    if lsn <= self._applied_lsn:
                        continue  # idempotent replay: already applied
                    self._state[key] = value
                    self._applied_lsn = lsn
            if corrupt:
                if sid == last_seg:
                    # Torn tail: truncate to the last valid record.
                    os.truncate(path, valid_end)
                else:
                    raise CorruptLogError(
                        "segment %s is corrupt and is not the last segment; "
                        "manual repair required" % path)

        # Drop segments fully covered by the checkpoint (a crash may have
        # interrupted deletion after the checkpoint was committed).
        removed = False
        for sid in seg_ids:
            if sid < cp_seg:
                os.remove(self._seg_path(sid))
                removed = True
        if removed:
            self._fsync_dir()

        # Choose the current segment: reuse the newest one, or start at the
        # segment recorded by the checkpoint.
        if last_seg is not None:
            self._seg_id = last_seg
        else:
            self._seg_id = max(cp_seg, 1)
        path = self._seg_path(self._seg_id)
        self._fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        self._seg_size = os.path.getsize(path)
        return self

    # -------------------------------------------------------------- mutation

    def set(self, key, value):
        """Append the write to the log, then apply it to memory."""
        if not isinstance(key, str):
            raise TypeError("keys must be str")
        with self._lock:
            lsn = self._applied_lsn + 1
            data = _encode_record(lsn, key, value)
            if self._seg_size > 0 and self._seg_size + len(data) > self._max_seg:
                self._rotate_locked()
            self._fault("log_write", fd=self._fd, data=data)
            os.write(self._fd, data)
            self._fault("log_written", fd=self._fd)
            if self._fsync:
                os.fsync(self._fd)
            self._seg_size += len(data)
            self._state[key] = value
            self._applied_lsn = lsn
            return lsn

    def get(self, key, default=None):
        return self._state.get(key, default)

    def __len__(self):
        return len(self._state)

    @property
    def applied_lsn(self):
        return self._applied_lsn

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    # ------------------------------------------------------------ checkpoint

    def checkpoint(self):
        """Snapshot state and record the log position; rotate the segment.

        A concurrent write is either included in the snapshot (it happened
        before the lock was taken) or lands in the new log segment and is
        replayed on recovery -- it can never be lost.
        """
        with self._lock:
            self._rotate_locked()
            data = _encode_checkpoint(self._applied_lsn, self._seg_id,
                                      self._state)
            tmp = os.path.join(self._dir, CHECKPOINT_TMP)
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._fault("checkpoint_tmp_written", path=tmp)
            os.replace(tmp, os.path.join(self._dir, CHECKPOINT_FILE))
            self._fsync_dir()
            self._fault("checkpoint_committed")
            # Delete segments fully covered by this checkpoint.
            for name in os.listdir(self._dir):
                if name.endswith(SEG_SUFFIX) and \
                        int(name[: -len(SEG_SUFFIX)]) < self._seg_id:
                    os.remove(os.path.join(self._dir, name))
            self._fsync_dir()

    # -------------------------------------------------------------- internal

    def _seg_path(self, sid):
        return os.path.join(self._dir, "%06d%s" % (sid, SEG_SUFFIX))

    def _rotate_locked(self):
        if self._fd >= 0:
            os.fsync(self._fd)
            os.close(self._fd)
        self._seg_id += 1
        self._fd = os.open(self._seg_path(self._seg_id),
                           os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        self._seg_size = 0
        self._fsync_dir()
        self._fault("rotate", segment=self._seg_id)

    def _fsync_dir(self):
        dfd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def close(self):
        with self._lock:
            if self._fd >= 0:
                os.fsync(self._fd)
                os.close(self._fd)
                self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
