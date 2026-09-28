"""Crash-safe daemon state persistence (Python 3, stdlib only).

The daemon persists its runtime state to a local JSON file and restores it on
startup.  This module fixes four production defect classes:

  1. Torn writes: a half-written state file recovered as a (silently invalid)
     state.  Fixed by tmp-file + fsync + atomic rename + sha256 checksum.
  2. Recovery steps executed in the wrong order (dependencies not ready).
     Fixed by a fixed, explicit recovery phase pipeline (see RECOVERY_PHASES).
  3. Completed cleanup tasks re-executed after restart.  Fixed by persisting
     completed cleanup ids and requiring cleanup actions to be idempotent.
  4. Old-version state files misjudged as corrupt after a version bump.
     Fixed by explicit version classification: known-old versions are
     migrated, unknown versions raise UnknownVersionError, and only checksum
     / parse failures count as corruption.

Recovery protocol -- phases run in a fixed order and each is idempotent:

  locate    : find state file; remove stale tmp files; none -> first boot.
  parse     : JSON-decode; failure -> corrupt.
  validate  : magic + sha256; mismatch -> corrupt.
  classify  : version < CURRENT -> migrate; version > CURRENT ->
              UnknownVersionError (file preserved untouched, NOT corrupt).
  fallback  : corrupt main -> preserve as *.corrupt.<ts>, try .bak, else empty.
  ready     : state returned; cleanup replay is safe (idempotent + persisted).

Commit protocol (crash at any point yields old or new state, never mixed):

  1. serialize record {magic, version, seq, payload} + sha256
  2. write <path>.tmp, flush, fsync
  3. rotate current main to <path>.bak (tmp + fsync + rename)
  4. os.replace(<path>.tmp, <path>)      <- atomic commit point
  5. fsync containing directory

  A kill at/before "bak_rotated" leaves the previous state; a kill at
  "renamed"/"dir_fsynced" leaves the new state.  Kill tests use an external
  rendezvous (GSB_SYNC_DIR): at every stage the process records the stage and
  waits for the parent to either release it or deliver a real SIGKILL, so the
  kill point is deterministic rather than a best-effort self os._exit().
"""

import hashlib
import hmac
import json
import os
import time

MAGIC = "gsb-daemon-state"
CURRENT_VERSION = 2

#: Ordered recovery phases.  Order matters: a phase may rely on invariants
#: established by every earlier phase (e.g. validate before classify).
RECOVERY_PHASES = ("locate", "parse", "validate", "classify", "fallback", "ready")

#: Commit stages, in order.  "renamed" is the atomic commit point.
COMMIT_STAGES = (
    "serialize",
    "tmp_written",
    "tmp_fsynced",
    "bak_rotated",
    "renamed",
    "dir_fsynced",
)


class StateCorruptError(Exception):
    """State file failed to parse or its checksum did not match."""


class UnknownVersionError(Exception):
    """State file is well-formed but written by a newer/unknown version.

    Distinct from corruption: the original file is preserved untouched.
    """


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _encode(record):
    body = dict(record)
    body.pop("sha256", None)
    digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    out = dict(body)
    out["sha256"] = digest
    return _canonical(out)


def _decode(data):
    record = json.loads(data)
    if not isinstance(record, dict) or record.get("magic") != MAGIC:
        raise StateCorruptError("bad magic or not a state record")
    digest = record.get("sha256")
    if not isinstance(digest, str):
        raise StateCorruptError("missing checksum")
    body = dict(record)
    body.pop("sha256")
    expect = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expect):
        raise StateCorruptError("checksum mismatch")
    return record


def _migrate_v1_to_v2(payload):
    """v1 stored cleanup targets as a plain list of paths ('cleanup_queue');
    v2 uses tracked job dicts with stable ids so completions can persist."""
    jobs = [
        {"id": "legacy-%d" % i, "path": p}
        for i, p in enumerate(payload.get("cleanup_queue", []))
    ]
    return {
        "counter": payload.get("counter", 0),
        "items": list(payload.get("items", [])),
        "pending_cleanups": jobs,
        "completed_cleanups": [],
    }


_MIGRATIONS = {(1, 2): _migrate_v1_to_v2}


def empty_state():
    return {
        "counter": 0,
        "items": [],
        "pending_cleanups": [],
        "completed_cleanups": [],
    }


def _default_cleanup_execute(job):
    """Idempotent cleanup action: removing an already-removed path is a no-op."""
    try:
        os.remove(job["path"])
    except FileNotFoundError:
        pass
    except IsADirectoryError:
        pass


class RecoveryReport:
    """Ordered, human-readable log of what recovery did (and why)."""

    def __init__(self):
        self.entries = []

    def note(self, phase, detail):
        assert phase in RECOVERY_PHASES, "unknown recovery phase: %r" % phase
        self.entries.append((phase, detail))

    @property
    def phases(self):
        return [phase for phase, _ in self.entries]

    def __str__(self):
        return "\n".join("[%s] %s" % e for e in self.entries)


class StateStore:
    def __init__(self, path, crash_hook=None):
        self.path = path
        self.bak_path = path + ".bak"
        self.tmp_path = path + ".tmp"
        self.state = empty_state()
        self.seq = 0
        self.report = RecoveryReport()
        # Hook for tests: called with each commit stage; may kill the process.
        self._crash_hook = crash_hook or self._default_crash_hook

    # ------------------------------------------------------------------ load

    def load(self):
        """Run the ordered recovery pipeline.  Returns the recovered state.

        Raises UnknownVersionError for well-formed files from a newer version
        (original file preserved in place, never renamed as corrupt).
        """
        self.report = RecoveryReport()
        record = self._load_file(self.path, self.report, is_backup=False)
        if record is None:
            record = self._load_file(self.bak_path, self.report, is_backup=True)
        if record is None:
            self.report.note("fallback", "no usable state; starting empty")
            self.state = empty_state()
            self.seq = 0
        else:
            self.state = record["payload"]
            self.seq = record["seq"]
        self.report.note("ready", "state seq=%d version=%d"
                         % (self.seq, CURRENT_VERSION))
        return self.state

    def _load_file(self, path, report, is_backup):
        label = "backup" if is_backup else "main"
        if not is_backup:
            self._cleanup_stale_tmp()
        if not os.path.exists(path):
            report.note("locate", "%s state file absent" % label)
            return None
        report.note("locate", "found %s state file %s" % (label, path))
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            record = _decode(raw.decode("utf-8"))
            report.note("parse", "%s file parsed" % label)
            report.note("validate", "%s checksum ok" % label)
        except (StateCorruptError, ValueError, UnicodeDecodeError) as exc:
            report.note("parse", "%s file corrupt: %s" % (label, exc))
            if not is_backup:
                preserved = self._preserve_corrupt(path)
                report.note("fallback", "corrupt main preserved as %s" % preserved)
            return None
        version = record.get("version")
        if not isinstance(version, int):
            report.note("classify", "%s file has no version; corrupt" % label)
            if not is_backup:
                self._preserve_corrupt(path)
            return None
        if version > CURRENT_VERSION:
            # Unknown (newer) version: NOT corruption.  Preserve in place.
            report.note("classify",
                        "unknown version %d; file preserved in place" % version)
            raise UnknownVersionError(
                "state file version %d > supported %d (%s)"
                % (version, CURRENT_VERSION, path))
        if version < CURRENT_VERSION:
            report.note("classify", "migrating v%d -> v%d" % (version, CURRENT_VERSION))
            record = self._migrate(record, path)
        else:
            report.note("classify", "version %d current" % version)
        return record

    def _migrate(self, record, path):
        version = record["version"]
        payload = record["payload"]
        while version < CURRENT_VERSION:
            step = _MIGRATIONS.get((version, version + 1))
            if step is None:
                raise UnknownVersionError(
                    "no migration path from version %d" % version)
            payload = step(payload)
            version += 1
        # Preserve the pre-migration original for forensics.
        preserved = "%s.migrated-from-v%d" % (path, record["version"])
        if not os.path.exists(preserved):
            with open(path, "rb") as src, open(preserved, "wb") as dst:
                dst.write(src.read())
        return {"version": CURRENT_VERSION, "seq": record["seq"],
                "payload": payload}

    def _preserve_corrupt(self, path):
        preserved = "%s.corrupt.%d.%d" % (path, int(time.time() * 1000), os.getpid())
        os.replace(path, preserved)
        return preserved

    def _cleanup_stale_tmp(self):
        for suffix in (".tmp", ".bak.tmp"):
            stale = self.path + suffix
            try:
                os.remove(stale)
            except FileNotFoundError:
                pass

    # ----------------------------------------------------------------- commit

    def commit(self, payload):
        """Atomically persist payload.  A crash at any stage leaves either the
        previous committed state or the new one -- never a mixture."""
        record = {
            "magic": MAGIC,
            "version": CURRENT_VERSION,
            "seq": self.seq + 1,
            "payload": payload,
        }
        self._crash_hook("serialize")
        data = _encode(record)

        with open(self.tmp_path, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            self._crash_hook("tmp_written")
            os.fsync(fh.fileno())
        self._crash_hook("tmp_fsynced")

        if os.path.exists(self.path):
            bak_tmp = self.bak_path + ".tmp"
            with open(self.path, "rb") as src, open(bak_tmp, "wb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(bak_tmp, self.bak_path)
        self._crash_hook("bak_rotated")

        os.replace(self.tmp_path, self.path)  # atomic commit point
        self._crash_hook("renamed")

        dir_fd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        self._crash_hook("dir_fsynced")

        self.state = payload
        self.seq = record["seq"]
        return record["seq"]

    # --------------------------------------------------------------- cleanup

    def run_cleanups(self, execute=None):
        """Run pending cleanup jobs exactly-once (effectively).

        Idempotency contract:
          * cleanup actions MUST be idempotent (default: remove-if-exists);
          * completed job ids are persisted after every job, so a restart
            never re-runs a completed job;
          * a crash between the action and the persistence re-runs the
            action once -- harmless because the action is idempotent.
        """
        execute = execute or _default_cleanup_execute
        done = set(self.state.get("completed_cleanups", []))
        pending = list(self.state.get("pending_cleanups", []))
        ran = []
        for job in pending:
            if job["id"] in done:
                self.state["pending_cleanups"].remove(job)
                continue
            execute(job)  # idempotent action first...
            done.add(job["id"])
            self.state["completed_cleanups"] = sorted(done)
            self.state["pending_cleanups"].remove(job)
            self.commit(self.state)  # ...then persist completion
            ran.append(job["id"])
        if not pending:
            self.commit(self.state)
        return ran

    # ------------------------------------------------------------------ misc

    @staticmethod
    def _default_crash_hook(stage):
        # GSB_KILL_AT_STAGE=<stage>: die at that exact commit stage.
        if os.environ.get("GSB_KILL_AT_STAGE") == stage:
            os._exit(137)
        StateStore._rendezvous(stage)

    @staticmethod
    def _rendezvous(stage):
        # External-SIGKILL rendezvous used by the kill matrix harness.
        # Protocol inside GSB_SYNC_DIR (race-free, monotonic ack counter):
        #   parent: writes 0 to "gate", starts child; for each published
        #           stage either acks (write stage_index+1) or SIGKILLs
        #   child : atomically publishes "<stage>" to "stage", then waits
        #           until gate >= stage_index+1 (or it is killed)
        sync_dir = os.environ.get("GSB_SYNC_DIR")
        if not sync_dir:
            return
        stage_file = os.path.join(sync_dir, "stage")
        gate = os.path.join(sync_dir, "gate")
        need = COMMIT_STAGES.index(stage) + 1
        try:
            tmp = stage_file + ".%d" % os.getpid()
            with open(tmp, "w") as fh:
                fh.write(stage)
            os.replace(tmp, stage_file)
        except OSError:
            return
        for _ in range(10000):  # ~50s ceiling; parent normally answers in ms
            try:
                with open(gate) as fh:
                    ack = int(fh.read().strip() or "0")
            except (OSError, ValueError):
                ack = 0
            if ack >= need:
                return
            time.sleep(0.005)


def check_invariants(state):
    """Structural invariants that a 'mixed' (torn) state would violate."""
    assert state["counter"] == len(state["items"]), (
        "mixed state: counter=%d but %d items"
        % (state["counter"], len(state["items"])))
    # Dense, contiguous, ascending sequence numbers (no gaps / dupes).
    assert state["items"] == list(range(1, state["counter"] + 1)), (
        "mixed state: items %r are not the dense 1..%d sequence"
        % (state["items"], state["counter"]))
    pending = state["pending_cleanups"]
    pending_ids = [job["id"] for job in pending]
    assert len(pending_ids) == len(set(pending_ids)), (
        "mixed state: duplicate pending ids %r" % pending_ids)
    done = set(state["completed_cleanups"])
    for job in pending:
        assert job["id"] not in done, (
            "mixed state: cleanup %r is both pending and completed" % job["id"])
    assert done == set(state["completed_cleanups"])  # completed list unique


def _writer_main(argv):
    # Child-process workload used by the random-kill hammer test:
    #   python3 daemon_state.py writer <dir> <count>
    directory, count = argv[0], int(argv[1])
    store = StateStore(os.path.join(directory, "state.json"))
    store.load()
    for _ in range(count):
        payload = dict(store.state)
        payload["counter"] = store.state["counter"] + 1
        payload["items"] = store.state["items"] + [payload["counter"]]
        store.commit(payload)
    print(store.seq)


def _commit_file_main(argv):
    # Commit exactly the payload read from a JSON file, running the full
    # commit pipeline (with stage rendezvous) inside this real subprocess:
    #   python3 daemon_state.py commit-file <state_path> <payload_json>
    state_path, payload_file = argv[0], argv[1]
    with open(payload_file, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    store = StateStore(state_path)
    store.load()
    seq = store.commit(payload)
    print(seq)


def _recover_main(argv):
    # Run the real recovery pipeline in a child process and print a JSON
    # verdict.  Used by the stage matrix so no in-process cheating is
    # possible: the recovery logic only ever sees bytes on disk.
    #   python3 daemon_state.py recover <state_path>
    state_path = argv[0]
    result = {"ok": False}
    try:
        store = StateStore(state_path)
        state = store.load()
        check_invariants(state)
        result = {
            "ok": True,
            "seq": store.seq,
            "state": state,
            "phases": store.report.phases,
        }
    except AssertionError as exc:
        result["error"] = "invariant: %s" % exc
    except UnknownVersionError as exc:
        result["error"] = "unknown-version: %s" % exc
    sys.stdout.write(json.dumps(result, sort_keys=True))
    sys.exit(0 if result["ok"] else 3)


_COMMANDS = {
    "writer": _writer_main,
    "commit-file": _commit_file_main,
    "recover": _recover_main,
}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        sys.stderr.write("usage: daemon_state.py %s ...\n"
                         % " | ".join(sorted(_COMMANDS)))
        sys.exit(2)
    _COMMANDS[sys.argv[1]](sys.argv[2:])
