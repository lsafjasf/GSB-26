"""Snapshot-chain incremental backup library (Python 3, stdlib only).

Storage layout under the store root:

    <root>/snapshots/<snapshot_id>.json   # one record per snapshot
    <root>/objects/<hh>/<sha256>          # content-addressed file blobs

A FULL snapshot records a complete manifest (path -> {sha256, size}).
An INCREMENTAL snapshot records its parent id plus the diff (upserts/deletes)
relative to the parent's effective state.  Every record carries a SHA-256
checksum over its canonical JSON, so any tampering is detectable.

Restore validates the whole chain (existence, checksum, format version,
topology, and every referenced content object) before writing anything, and
fails with a distinct, locateable error instead of producing partial data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

FORMAT_VERSION = 1
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# Errors (each carries .snapshot_id identifying the break point)
# ---------------------------------------------------------------------------
class BackupError(Exception):
    """Base class for all backup/restore failures."""

    def __init__(self, message, snapshot_id=None):
        super().__init__(message)
        self.snapshot_id = snapshot_id


class SnapshotNotFoundError(BackupError):
    """A snapshot record referenced by the chain does not exist."""


class SnapshotCorruptError(BackupError):
    """A snapshot record is unparseable, malformed, or checksum-mismatched."""


class VersionMismatchError(BackupError):
    """A snapshot record uses an unsupported format version."""


class ChainBrokenError(BackupError):
    """The chain topology is inconsistent (bad parent link, cycle, ...)."""


class ObjectCorruptError(BackupError):
    """A content object referenced by a snapshot is missing or corrupted."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_checksum(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "checksum"}
    return _hash_bytes(_canonical(body))


def _check_snapshot_id(snapshot_id) -> str:
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("snapshot id must be a non-empty string")
    if snapshot_id in (".", "..") or "/" in snapshot_id or "\\" in snapshot_id:
        raise ValueError(f"illegal snapshot id: {snapshot_id!r}")
    return snapshot_id


def _scan_tree(source: Path) -> dict:
    """Map posix relpath -> {"sha256", "size"} for every regular file."""
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue  # symlinks / non-regular files are not backed up
            rel = full.relative_to(source).as_posix()
            manifest[rel] = {"sha256": _hash_file(full), "size": full.stat().st_size}
    return manifest


def _fold_state(records) -> dict:
    """Fold a validated chain (full -> target) into the effective manifest."""
    state: dict = {}
    for rec in records:
        if rec["kind"] == "full":
            state = dict(rec["files"])
        else:
            for rel in rec["deletes"]:
                state.pop(rel, None)
            state.update(rec["upserts"])
    return state


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)
        self.snap_dir = self.root / "snapshots"
        self.obj_dir = self.root / "objects"
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        self.obj_dir.mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------
    def _snapshot_path(self, snapshot_id) -> Path:
        return self.snap_dir / (_check_snapshot_id(snapshot_id) + ".json")

    def _object_path(self, digest: str) -> Path:
        return self.obj_dir / digest[:2] / digest

    # ---- object ingest -----------------------------------------------------
    def _store_object(self, src: Path, digest: str) -> None:
        dst = self._object_path(digest)
        if dst.exists():
            return  # content-addressed: identical content already stored
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp-" + uuid.uuid4().hex)
        h = hashlib.sha256()
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                for chunk in iter(lambda: fin.read(_CHUNK), b""):
                    h.update(chunk)
                    fout.write(chunk)
            if h.hexdigest() != digest:
                raise BackupError(f"source file changed during backup: {src}")
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)

    def _ingest(self, source: Path, manifest: dict) -> None:
        for rel, meta in manifest.items():
            self._store_object(source / rel, meta["sha256"])

    # ---- records -----------------------------------------------------------
    def _write_record(self, record: dict) -> None:
        record["checksum"] = _record_checksum(record)
        path = self._snapshot_path(record["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _validate_schema(record: dict, snapshot_id: str) -> None:
        def bad(why):
            raise SnapshotCorruptError(
                f"snapshot {snapshot_id!r}: malformed record ({why})", snapshot_id)

        if record.get("id") != snapshot_id:
            raise ChainBrokenError(
                f"snapshot file {snapshot_id!r} contains record id "
                f"{record.get('id')!r}", snapshot_id)
        if record.get("kind") not in ("full", "incremental"):
            bad("kind must be 'full' or 'incremental'")
        if not (record.get("parent") is None or isinstance(record.get("parent"), str)):
            bad("parent must be null or a string")
        if not isinstance(record.get("created_at"), (int, float)):
            bad("created_at must be a number")

        def check_manifest(m, key):
            if not isinstance(m, dict):
                bad(f"{key} must be an object")
            for rel, meta in m.items():
                if not isinstance(rel, str) or not isinstance(meta, dict):
                    bad(f"{key} entries must map string -> object")
                dig, size = meta.get("sha256"), meta.get("size")
                if not (isinstance(dig, str) and len(dig) == 64
                        and all(c in "0123456789abcdef" for c in dig)):
                    bad(f"{key}[{rel!r}]: invalid sha256")
                if not (isinstance(size, int) and size >= 0):
                    bad(f"{key}[{rel!r}]: invalid size")

        if record["kind"] == "full":
            if "files" not in record:
                bad("full snapshot missing 'files'")
            check_manifest(record["files"], "files")
        else:
            if "upserts" not in record or "deletes" not in record:
                bad("incremental snapshot missing 'upserts'/'deletes'")
            check_manifest(record["upserts"], "upserts")
            if not (isinstance(record["deletes"], list)
                    and all(isinstance(p, str) for p in record["deletes"])):
                bad("deletes must be a list of strings")

    def _load_record(self, snapshot_id) -> dict:
        path = self._snapshot_path(snapshot_id)
        if not path.is_file():
            raise SnapshotNotFoundError(
                f"snapshot {snapshot_id!r} not found", snapshot_id)
        try:
            record = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SnapshotCorruptError(
                f"snapshot {snapshot_id!r}: unparseable record ({exc})",
                snapshot_id) from exc
        if not isinstance(record, dict):
            raise SnapshotCorruptError(
                f"snapshot {snapshot_id!r}: record is not a JSON object",
                snapshot_id)
        checksum = record.get("checksum")
        if not isinstance(checksum, str) or checksum != _record_checksum(record):
            raise SnapshotCorruptError(
                f"snapshot {snapshot_id!r}: checksum mismatch "
                f"(record tampered or corrupted)", snapshot_id)
        version = record.get("format_version")
        if version != FORMAT_VERSION:
            raise VersionMismatchError(
                f"snapshot {snapshot_id!r}: format_version {version!r} is not "
                f"supported (expected {FORMAT_VERSION})", snapshot_id)
        self._validate_schema(record, snapshot_id)
        return record

    # ---- chain -------------------------------------------------------------
    def chain(self, snapshot_id) -> list:
        """Return validated records ordered full -> target (no object checks)."""
        _check_snapshot_id(snapshot_id)
        order = []
        seen = set()
        current = snapshot_id
        child = None
        while True:
            if current in seen:
                raise ChainBrokenError(
                    f"cycle in snapshot chain at {current!r}", current)
            seen.add(current)
            try:
                record = self._load_record(current)
            except SnapshotNotFoundError as exc:
                if child is not None:
                    raise SnapshotNotFoundError(
                        f"chain broken: snapshot {child!r} depends on missing "
                        f"parent {current!r}", current) from exc
                raise
            order.append(record)
            parent = record["parent"]
            if record["kind"] == "full":
                if parent is not None:
                    raise ChainBrokenError(
                        f"full snapshot {current!r} must not declare a parent "
                        f"(declares {parent!r})", current)
                break
            if parent is None:
                raise ChainBrokenError(
                    f"incremental snapshot {current!r} declares no parent",
                    current)
            child = current
            current = parent
        order.reverse()
        return order

    def validate_chain(self, snapshot_id, verify_objects: bool = True) -> list:
        """Validate the whole chain up to *snapshot_id*.

        Checks (in order, each with its own error type and break point):
        record existence -> checksum -> format version -> schema/topology ->
        referenced content objects.  Returns records ordered full -> target.
        """
        records = self.chain(snapshot_id)
        if verify_objects:
            for rec in records:
                manifest = rec["files"] if rec["kind"] == "full" else rec["upserts"]
                for rel, meta in manifest.items():
                    obj = self._object_path(meta["sha256"])
                    if not obj.is_file():
                        raise ObjectCorruptError(
                            f"snapshot {rec['id']!r}: content object for "
                            f"{rel!r} is missing ({meta['sha256']})",
                            rec["id"])
                    if _hash_file(obj) != meta["sha256"]:
                        raise ObjectCorruptError(
                            f"snapshot {rec['id']!r}: content object for "
                            f"{rel!r} failed hash verification "
                            f"({meta['sha256']})", rec["id"])
        return records

    def effective_state(self, snapshot_id, verify_objects: bool = False) -> dict:
        return _fold_state(self.validate_chain(snapshot_id, verify_objects))

    # ---- backup ------------------------------------------------------------
    def _new_id(self, kind: str) -> str:
        return f"{kind}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"

    def create_full(self, source_dir, snapshot_id=None) -> str:
        source = Path(source_dir)
        snapshot_id = snapshot_id or self._new_id("full")
        manifest = _scan_tree(source)
        self._ingest(source, manifest)
        self._write_record({
            "format_version": FORMAT_VERSION,
            "id": snapshot_id,
            "kind": "full",
            "parent": None,
            "created_at": time.time(),
            "files": manifest,
        })
        return snapshot_id

    def create_incremental(self, source_dir, parent_id, snapshot_id=None) -> str:
        source = Path(source_dir)
        parent_state = self.effective_state(parent_id)  # validates parent chain
        snapshot_id = snapshot_id or self._new_id("inc")
        current = _scan_tree(source)
        upserts = {p: m for p, m in current.items() if parent_state.get(p) != m}
        deletes = sorted(set(parent_state) - set(current))
        self._ingest(source, upserts)
        self._write_record({
            "format_version": FORMAT_VERSION,
            "id": snapshot_id,
            "kind": "incremental",
            "parent": parent_id,
            "created_at": time.time(),
            "upserts": upserts,
            "deletes": deletes,
        })
        return snapshot_id

    # ---- restore -----------------------------------------------------------
    def restore(self, snapshot_id, dest_dir) -> dict:
        """Restore *snapshot_id* into *dest_dir* after full chain validation.

        The destination is synchronised to exactly match the snapshot state
        (extra files removed), so repeated restores are idempotent.  Nothing
        is written unless the entire chain validates first.
        """
        start = time.perf_counter()
        records = self.validate_chain(snapshot_id, verify_objects=True)
        state = _fold_state(records)

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        files_written = 0
        bytes_written = 0
        for rel in sorted(state):
            meta = state[rel]
            obj = self._object_path(meta["sha256"])
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
            h = hashlib.sha256()
            try:
                with open(obj, "rb") as fin, open(tmp, "wb") as fout:
                    for chunk in iter(lambda: fin.read(_CHUNK), b""):
                        h.update(chunk)
                        fout.write(chunk)
                if h.hexdigest() != meta["sha256"]:
                    raise ObjectCorruptError(
                        f"snapshot {snapshot_id!r}: content object for {rel!r} "
                        f"corrupted during read", snapshot_id)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            files_written += 1
            bytes_written += meta["size"]

        # remove anything not part of the snapshot state (idempotent restore)
        expected = set(state)
        for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
            d = Path(dirpath)
            for name in filenames:
                p = d / name
                if p.relative_to(dest).as_posix() not in expected:
                    p.unlink()
            if d != dest and not any(d.iterdir()):
                d.rmdir()

        return {
            "snapshot": snapshot_id,
            "chain_length": len(records),
            "files": files_written,
            "bytes": bytes_written,
            "seconds": time.perf_counter() - start,
        }

    # ---- misc --------------------------------------------------------------
    def snapshot_ids(self) -> list:
        return sorted(p.stem for p in self.snap_dir.glob("*.json"))

    def describe(self, snapshot_id) -> dict:
        rec = self._load_record(_check_snapshot_id(snapshot_id))
        return {k: rec[k] for k in
                ("id", "kind", "parent", "created_at", "format_version")}
