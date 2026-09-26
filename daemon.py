#!/usr/bin/env python3
"""Crash-safe daemon state persistence with ordered, idempotent recovery.

Write protocol (every save, no exceptions)
------------------------------------------
  1. serialize state as JSON with a SHA-256 checksum over the canonical body
  2. write bytes to ``state.json.tmp``, flush + fsync
  3. ``os.replace(tmp, state.json)``          (atomic on POSIX)
  4. fsync the containing directory
  5. rewrite ``state.json.bak`` with the same tmp+replace+fsync sequence

A kill -9 at any point leaves ``state.json`` holding either the previous
complete state or the new complete state.  A torn write can only ever exist
in the tmp file, which recovery removes and ignores.  ``state.json.bak``
always holds the most recent *validated* state, so even if the main file is
damaged externally (truncated, half-overwritten) recovery falls back to an
earlier complete state -- never a mixed one.

Recovery order (strictly sequential; each step starts only after the
previous one finished)
-----------------------
  step 0  remove stale tmp files left by a killed writer
  step 1  load + validate primary (JSON parse -> version check -> checksum
          -> schema).  Corruption and unknown version are distinct errors;
          the offending file is quarantined (renamed, bytes preserved) for
          later inspection.  On corruption, fall back to ``state.json.bak``.
  step 2  migrate known old versions to the current schema (pure function,
          no I/O)
  step 3  rebuild runtime dependencies (junk dir, file index).  No cleanup
          may run before dependencies are ready.
  step 4  run pending cleanups one at a time; after each cleanup the state
          is checkpointed (task moved pending -> done and saved atomically)
          so a kill between two cleanups never re-executes a checkpointed
          task.

Idempotency
-----------
* A cleanup task only acts when its resource still exists; re-running an
  already-executed cleanup is a no-op (no ledger write, no error).
* A checkpointed cleanup is never re-executed after a restart.
* Recovery is side-effect free except for quarantining bad state files and
  removing stale tmp files; running it twice yields the same state.
"""

import argparse
import hashlib
import json
import os
import sys
import time

CURRENT_VERSION = 2
KNOWN_VERSIONS = (1, 2)

STATE_NAME = "state.json"


class CorruptStateError(Exception):
    """State file exists but is not a valid, intact state (torn write,
    truncation, bad checksum, schema violation, empty file)."""


class UnknownVersionError(Exception):
    """State file is intact JSON but its version is not one we understand."""


def _maybe_crash(stage):
    """Fault-injection hook used only by the regression tests."""
    if os.environ.get("DAEMON_CRASH_AT") == stage:
        os._exit(1)


def _canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _checksum(body):
    return hashlib.sha256(_canonical(body)).hexdigest()


def fresh_state():
    return {
        "version": CURRENT_VERSION,
        "seq": 0,
        "work_count": 0,
        "pending_cleanups": [],
        "done_cleanups": [],
    }


def _validate_v1(data):
    if not isinstance(data.get("seq"), int):
        raise CorruptStateError("v1: bad seq")
    if not isinstance(data.get("work_count"), int):
        raise CorruptStateError("v1: bad work_count")
    if not isinstance(data.get("pending_cleanups"), list):
        raise CorruptStateError("v1: bad pending_cleanups")


def _validate_v2(data):
    checksum = data.get("checksum")
    if not isinstance(checksum, str):
        raise CorruptStateError("v2: missing checksum")
    body = {k: v for k, v in data.items() if k != "checksum"}
    if _checksum(body) != checksum:
        raise CorruptStateError("v2: checksum mismatch")
    if not isinstance(body.get("seq"), int):
        raise CorruptStateError("v2: bad seq")
    if not isinstance(body.get("work_count"), int):
        raise CorruptStateError("v2: bad work_count")
    if not isinstance(body.get("pending_cleanups"), list):
        raise CorruptStateError("v2: bad pending_cleanups")
    if not isinstance(body.get("done_cleanups"), list):
        raise CorruptStateError("v2: bad done_cleanups")


def validate_state(data):
    """Raise CorruptStateError / UnknownVersionError, or return None."""
    if not isinstance(data, dict):
        raise CorruptStateError("top level is not an object")
    version = data.get("version")
    if version is None:
        raise CorruptStateError("missing version field")
    if not isinstance(version, int):
        raise CorruptStateError("version is not an integer")
    if version not in KNOWN_VERSIONS:
        raise UnknownVersionError("unknown state version: %r" % (version,))
    if version == 1:
        _validate_v1(data)
    else:
        _validate_v2(data)


def migrate(data):
    """Pure function: bring a validated old-version state up to CURRENT_VERSION."""
    if data["version"] == 1:
        return {
            "version": CURRENT_VERSION,
            "seq": data["seq"],
            "work_count": data["work_count"],
            "pending_cleanups": list(data["pending_cleanups"]),
            "done_cleanups": [],
        }
    return {k: v for k, v in data.items() if k != "checksum"}


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StateStore:
    def __init__(self, directory):
        self.dir = directory
        self.path = os.path.join(directory, STATE_NAME)
        self.bak_path = self.path + ".bak"
        self.tmp_path = self.path + ".tmp"

    # ------------------------------------------------------------------ save
    def save(self, state):
        body = {k: v for k, v in state.items() if k != "checksum"}
        body["checksum"] = _checksum(body)
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self._atomic_write(self.path, data, main=True)
        self._atomic_write(self.bak_path, data, main=False)

    def _atomic_write(self, path, data, main):
        tmp = path + ".tmp"
        half = len(data) // 2
        with open(tmp, "wb") as fh:
            fh.write(data[:half])
            if main:
                _maybe_crash("tmp_partial")      # kill -> torn tmp only
            fh.write(data[half:])
            fh.flush()
            os.fsync(fh.fileno())
        if main:
            _maybe_crash("tmp_written")          # kill -> old main intact
        os.replace(tmp, path)
        if main:
            _maybe_crash("renamed")              # kill -> new main, old bak
        _fsync_dir(self.dir)
        if main:
            _maybe_crash("dir_fsynced")          # kill -> main/bak diverge
        else:
            _maybe_crash("backup_written")

    # --------------------------------------------------------------- recover
    def recover(self):
        """Return (state, source).  Never raises for bad on-disk data."""
        self._remove_stale_tmp()
        if not os.path.exists(self.path):
            if os.path.exists(self.bak_path):
                state = self._try_load(self.bak_path)
                if state is not None:
                    return state, "backup"
            return fresh_state(), "fresh"
        try:
            return self._load_validated(self.path), "primary"
        except UnknownVersionError:
            self._quarantine(self.path, ".unsupported")
            return fresh_state(), "unsupported"
        except CorruptStateError:
            self._quarantine(self.path, ".corrupt")
            state = self._try_load(self.bak_path)
            if state is not None:
                return state, "backup"
            return fresh_state(), "fresh-after-corrupt"

    def _remove_stale_tmp(self):
        for name in os.listdir(self.dir):
            if name.startswith(STATE_NAME) and name.endswith(".tmp"):
                try:
                    os.remove(os.path.join(self.dir, name))
                except OSError:
                    pass

    def _try_load(self, path):
        try:
            return self._load_validated(path)
        except (CorruptStateError, UnknownVersionError, OSError):
            return None

    def _load_validated(self, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        if not raw:
            raise CorruptStateError("empty state file")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CorruptStateError("unparseable: %s" % exc)
        validate_state(data)
        return migrate(data)

    def _quarantine(self, path, suffix):
        stamp = time.strftime("%Y%m%dT%H%M%S") + "-" + str(os.getpid())
        os.rename(path, path + suffix + "-" + stamp)


class Daemon:
    """Ordered, idempotent recovery on top of StateStore."""

    def __init__(self, directory):
        self.dir = directory
        self.junk_dir = os.path.join(directory, "junk")
        self.ledger_path = os.path.join(directory, "cleanup.log")
        self.order_path = os.path.join(directory, "recovery_order.log")
        self.store = StateStore(directory)
        self.state = None
        self.source = None
        self.deps_ready = False

    def _log_step(self, name):
        with open(self.order_path, "a", encoding="utf-8") as fh:
            fh.write(name + "\n")

    def recover(self):
        # step 1+2: load, validate, migrate (inside the store)
        self.state, self.source = self.store.recover()
        self._log_step("load")
        # step 3: rebuild runtime dependencies before any cleanup
        self._rebuild_dependencies()
        # step 4: idempotent cleanups with per-task checkpoint
        self._run_pending_cleanups()
        return self.source

    def _rebuild_dependencies(self):
        os.makedirs(self.junk_dir, exist_ok=True)
        self.junk_index = set(os.listdir(self.junk_dir))
        self.deps_ready = True
        self._log_step("deps")

    def _run_pending_cleanups(self):
        assert self.deps_ready, "cleanups must not run before dependencies"
        for task in list(self.state["pending_cleanups"]):
            self._execute_cleanup(task)  # idempotent
            _maybe_crash("cleanup_executed")  # kill after exec, before checkpoint
            if task in self.state["pending_cleanups"]:
                self.state["pending_cleanups"].remove(task)
            if task not in self.state["done_cleanups"]:
                self.state["done_cleanups"].append(task)
            self.state["seq"] += 1
            self.store.save(self.state)  # checkpoint after each task
            self._log_step("cleanup:" + task)

    def _execute_cleanup(self, task):
        # Idempotent: only acts when the resource still exists.  Re-running
        # a completed cleanup is a no-op -- no ledger write, no error.
        path = os.path.join(self.junk_dir, task + ".tmp")
        if os.path.exists(path):
            os.remove(path)
            with open(self.ledger_path, "a", encoding="utf-8") as fh:
                fh.write("removed %s\n" % task)

    # ------------------------------------------------------------- operations
    def do_work(self):
        self.state["work_count"] += 1
        self.state["seq"] += 1
        self.store.save(self.state)

    def schedule_cleanup(self, task):
        os.makedirs(self.junk_dir, exist_ok=True)
        junk = os.path.join(self.junk_dir, task + ".tmp")
        if not os.path.exists(junk):
            with open(junk, "w", encoding="utf-8") as fh:
                fh.write("junk\n")
        if task not in self.state["pending_cleanups"] \
                and task not in self.state["done_cleanups"]:
            self.state["pending_cleanups"].append(task)
            self.state["seq"] += 1
            self.store.save(self.state)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("command",
                        choices=["recover", "work", "schedule-cleanup", "dump"])
    parser.add_argument("task", nargs="?")
    args = parser.parse_args(argv)

    os.makedirs(args.dir, exist_ok=True)
    daemon = Daemon(args.dir)
    source = daemon.recover()

    if args.command == "work":
        daemon.do_work()
    elif args.command == "schedule-cleanup":
        if not args.task:
            parser.error("schedule-cleanup requires a task name")
        daemon.schedule_cleanup(args.task)

    json.dump({"source": source, "state": daemon.state}, sys.stdout,
              sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
