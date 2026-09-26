"""Write-ahead log (WAL) + checkpointed key-value store. Python 3 stdlib only.

Design
------
- Every mutation is appended to the current log segment (and fsynced) BEFORE
  the in-memory state is updated (write-ahead logging).
- Log records are framed as: [u32 length][u32 crc32][JSON payload].
  Each record carries a monotonically increasing LSN.
- A checkpoint is an atomic (tmp-file + os.replace + dir fsync) snapshot of
  the state together with the LSN and segment id it covers. Recovery loads
  the checkpoint and replays only records with lsn > checkpoint.lsn.
- Segments are rotated when they exceed `max_segment_bytes`. After a
  checkpoint, segments older than the checkpoint segment are garbage
  collected.
- Replay is idempotent: records with lsn <= last applied LSN are skipped,
  and set/del are naturally idempotent operations.
- On recovery, a segment with a corrupt/truncated tail is truncated at the last
  valid record and replay stops there (later segments are ignored).

Crash-injection hooks: `crash_hook(point)` is called at these points:
  "after_log_write"        - record written + fsynced, before mem apply
  "checkpoint_tmp_written" - checkpoint tmp file fsynced, before rename
  "checkpoint_renamed"     - checkpoint atomically renamed
  "rotate_closed"          - old segment closed, new one not yet opened
  "rotate_opened"          - new segment opened for append
  "gc_done"                - old segments garbage collected
"""

import json
import os
import struct
import threading
import zlib

_HEADER = struct.Struct("<II")  # payload length, crc32(payload)
_MAX_RECORD = 64 * 1024 * 1024
CHECKPOINT_NAME = "checkpoint.json"
_SEGMENT_PREFIX = "segment-"
_SEGMENT_SUFFIX = ".log"


def _encode_record(record):
    payload = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _HEADER.pack(len(payload), zlib.crc32(payload) & 0xFFFFFFFF) + payload


def scan_segment(path):
    """Return (records, valid_bytes). Stops at the first corrupt/truncated record."""
    records = []
    offset = 0
    with open(path, "rb") as f:
        while True:
            header = f.read(_HEADER.size)
            if len(header) < _HEADER.size:
                break
            length, crc = _HEADER.unpack(header)
            if length > _MAX_RECORD:
                break
            payload = f.read(length)
            if len(payload) < length or (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
                break
            records.append(json.loads(payload.decode("utf-8")))
            offset += _HEADER.size + length
    return records, offset


def _segment_name(sid):
    return "%s%06d%s" % (_SEGMENT_PREFIX, sid, _SEGMENT_SUFFIX)


def list_segment_ids(directory):
    ids = []
    for name in os.listdir(directory):
        if name.startswith(_SEGMENT_PREFIX) and name.endswith(_SEGMENT_SUFFIX):
            try:
                ids.append(int(name[len(_SEGMENT_PREFIX):-len(_SEGMENT_SUFFIX)]))
            except ValueError:
                pass
    return sorted(ids)


def load_checkpoint(directory):
    path = os.path.join(directory, CHECKPOINT_NAME)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WALKV:
    def __init__(self, directory, max_segment_bytes=64 * 1024 * 1024,
                 crash_hook=None, fsync=True, gc_segments=True):
        self._dir = directory
        os.makedirs(directory, exist_ok=True)
        self._max_segment_bytes = max_segment_bytes
        self._hook = crash_hook or (lambda point: None)
        self._fsync = fsync
        self._gc_enabled = gc_segments
        self._lock = threading.Lock()
        self._state = {}
        self._lsn = 0
        self._fd = -1
        self._segment_id = 0
        self._segment_size = 0
        self._recover()
        self._open_append_segment()

    # ---------------- recovery ----------------

    def _apply_record(self, record):
        if record["op"] == "set":
            self._state[record["key"]] = record["value"]
        elif record["op"] == "del":
            self._state.pop(record["key"], None)

    def _replay_record(self, record):
        lsn = record["lsn"]
        if lsn <= self._lsn:
            return  # idempotent: already covered by checkpoint / earlier replay
        self._apply_record(record)
        self._lsn = lsn

    def _recover(self):
        cp = load_checkpoint(self._dir)
        cp_segment = -1
        if cp is not None:
            self._state = dict(cp["state"])
            self._lsn = cp["lsn"]
            cp_segment = cp["segment"]
        last_id = None
        for sid in list_segment_ids(self._dir):
            if sid < cp_segment:
                continue
            path = os.path.join(self._dir, _segment_name(sid))
            records, valid = scan_segment(path)
            for record in records:
                self._replay_record(record)
            if valid < os.path.getsize(path):
                # corrupt/truncated tail: drop everything after last valid record
                with open(path, "r+b") as f:
                    f.truncate(valid)
                last_id = sid
                break  # stop at the first corrupt segment
            last_id = sid
        self._segment_id = last_id if last_id is not None else max(cp_segment, 0)

    def _open_append_segment(self):
        path = os.path.join(self._dir, _segment_name(self._segment_id))
        new = not os.path.exists(path)
        self._fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
        self._segment_size = os.path.getsize(path)
        if new:
            _fsync_dir(self._dir)

    # ---------------- public API ----------------

    def set(self, key, value):
        return self._write({"op": "set", "key": key, "value": value})

    def delete(self, key):
        return self._write({"op": "del", "key": key})

    def get(self, key, default=None):
        with self._lock:
            return self._state.get(key, default)

    def items(self):
        with self._lock:
            return dict(self._state)

    def __len__(self):
        with self._lock:
            return len(self._state)

    @property
    def lsn(self):
        with self._lock:
            return self._lsn

    def checkpoint(self):
        """Atomically snapshot state; returns the checkpoint dict."""
        with self._lock:
            if self._fd >= 0:
                os.fsync(self._fd)  # log durable up to the snapshot LSN first
            cp = {"lsn": self._lsn, "segment": self._segment_id,
                  "state": dict(self._state)}
            payload = json.dumps(cp, separators=(",", ":"), sort_keys=True).encode("utf-8")
            tmp = os.path.join(self._dir, CHECKPOINT_NAME + ".tmp")
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            self._hook("checkpoint_tmp_written")
            os.replace(tmp, os.path.join(self._dir, CHECKPOINT_NAME))
            _fsync_dir(self._dir)
            self._hook("checkpoint_renamed")
            if self._gc_enabled:
                self._gc_segments(cp["segment"])
            return cp

    def close(self):
        with self._lock:
            if self._fd >= 0:
                if self._fsync:
                    os.fsync(self._fd)
                os.close(self._fd)
                self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- internals ----------------

    def _write(self, record):
        with self._lock:
            self._lsn += 1
            record["lsn"] = self._lsn
            data = _encode_record(record)
            if self._segment_size > 0 and \
                    self._segment_size + len(data) > self._max_segment_bytes:
                self._rotate()
            os.write(self._fd, data)
            if self._fsync:
                os.fsync(self._fd)
            self._segment_size += len(data)
            self._hook("after_log_write")
            self._apply_record(record)
            return self._lsn

    def _rotate(self):
        if self._fsync:
            os.fsync(self._fd)
        os.close(self._fd)
        self._hook("rotate_closed")
        self._segment_id += 1
        self._open_append_segment()
        self._hook("rotate_opened")

    def _gc_segments(self, keep_from):
        for sid in list_segment_ids(self._dir):
            if sid < keep_from:
                os.unlink(os.path.join(self._dir, _segment_name(sid)))
        _fsync_dir(self._dir)
        self._hook("gc_done")
