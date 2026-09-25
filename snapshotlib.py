"""快照链与增量备份库（仅使用 Python 标准库）。

存储模型：
    repo/
      objects/<sha256>              # 按内容寻址的数据块（去重）
      snapshots/<snapshot_id>/manifest.json
      HEAD                          # 最新快照 id

每个清单（manifest）记录：
    version               格式版本
    id                    快照标识
    type                  "full" | "incremental"
    parent_id             父快照 id（全量为 None）
    parent_hash           父快照清单的校验值（增量必填）
    state_hash            本快照时刻完整状态（路径->内容哈希）的校验值
    changes               本快照相对父快照的 upserts / deletes
    manifest_hash         清单自身校验值（防篡改）

恢复时从目标快照沿父链回溯到全量根，逐级校验：
    父快照缺失        -> MissingSnapshotError
    清单/状态校验失败  -> ChecksumMismatchError
    版本不匹配        -> VersionMismatchError
    链断裂（父引用与父实际校验值不符）-> BrokenChainError
    数据块缺失/损坏    -> CorruptObjectError
"""

import hashlib
import json
import os
import shutil
import time
import uuid

FORMAT_VERSION = 1

__all__ = [
    "FORMAT_VERSION",
    "ChainError",
    "MissingSnapshotError",
    "VersionMismatchError",
    "ChecksumMismatchError",
    "BrokenChainError",
    "CorruptObjectError",
    "RestoreReport",
    "BackupRepo",
    "compute_manifest_hash",
    "scan_dir",
]


# ---------------------------------------------------------------- 错误类型

class ChainError(Exception):
    """链校验 / 恢复错误的公共基类。"""


class MissingSnapshotError(ChainError):
    """链中引用的快照在库中不存在。"""

    def __init__(self, snapshot_id, referenced_by=None):
        self.snapshot_id = snapshot_id
        self.referenced_by = referenced_by
        msg = "快照缺失: %s" % snapshot_id
        if referenced_by:
            msg += "（被快照 %s 引用为父快照，断点在此处）" % referenced_by
        super().__init__(msg)


class VersionMismatchError(ChainError):
    """快照格式版本与本库不支持。"""

    def __init__(self, snapshot_id, found, expected):
        self.snapshot_id = snapshot_id
        self.found = found
        self.expected = expected
        super().__init__(
            "版本不匹配: 快照 %s 的格式版本为 %r，本库支持 %r"
            % (snapshot_id, found, expected)
        )


class ChecksumMismatchError(ChainError):
    """清单或状态校验值与内容不符（被篡改或损坏）。"""


class BrokenChainError(ChainError):
    """链断裂：子快照声明的父引用与父快照实际校验值不一致。"""


class CorruptObjectError(ChainError):
    """数据块缺失或内容校验失败。"""


# ---------------------------------------------------------------- 工具函数

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_manifest_hash(manifest):
    """计算清单校验值（覆盖除 manifest_hash 外的所有字段）。"""
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _state_hash(state):
    """state: {rel_path: sha256} -> 整体状态校验值。"""
    return hashlib.sha256(_canonical(state)).hexdigest()


def scan_dir(root):
    """扫描目录，返回 {相对路径('/'分隔): (sha256, size)}，仅含普通文件。"""
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            state[rel] = (_sha256_file(full), os.path.getsize(full))
    return state


def _check_rel_path(rel):
    if rel.startswith("/") or rel.startswith("..") or "/../" in rel:
        raise ChecksumMismatchError("清单包含非法路径: %r" % rel)


# ---------------------------------------------------------------- 恢复报告

class RestoreReport:
    def __init__(self, snapshot_id, chain_length, files, bytes_restored, elapsed):
        self.snapshot_id = snapshot_id
        self.chain_length = chain_length
        self.files = files
        self.bytes_restored = bytes_restored
        self.elapsed_seconds = elapsed

    def __repr__(self):
        return (
            "RestoreReport(snapshot=%s, chain_length=%d, files=%d, "
            "bytes=%d, elapsed=%.4fs)"
            % (self.snapshot_id, self.chain_length, self.files,
               self.bytes_restored, self.elapsed_seconds)
        )


# ---------------------------------------------------------------- 备份库

class BackupRepo:
    def __init__(self, path):
        self.path = path
        self.objects_dir = os.path.join(path, "objects")
        self.snapshots_dir = os.path.join(path, "snapshots")
        os.makedirs(self.objects_dir, exist_ok=True)
        os.makedirs(self.snapshots_dir, exist_ok=True)

    # ---------------- 内部：清单读写 ----------------

    def _manifest_path(self, snapshot_id):
        return os.path.join(self.snapshots_dir, snapshot_id, "manifest.json")

    def _load_manifest(self, snapshot_id, referenced_by=None):
        mpath = self._manifest_path(snapshot_id)
        if not os.path.exists(mpath):
            raise MissingSnapshotError(snapshot_id, referenced_by=referenced_by)
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError) as exc:
            raise ChecksumMismatchError(
                "快照 %s 的清单无法解析（可能已损坏）: %s" % (snapshot_id, exc)
            )

    def _check_manifest(self, manifest, expected_id):
        sid = manifest.get("id")
        if sid != expected_id:
            raise BrokenChainError(
                "链断裂: 目录 %s 中的清单声明的 id 为 %r" % (expected_id, sid)
            )
        version = manifest.get("version")
        if version != FORMAT_VERSION:
            raise VersionMismatchError(sid, version, FORMAT_VERSION)
        actual = compute_manifest_hash(manifest)
        if actual != manifest.get("manifest_hash"):
            raise ChecksumMismatchError(
                "校验失败: 快照 %s 的清单内容与其校验值不符（被篡改或损坏）" % sid
            )

    # ---------------- 内部：链校验 ----------------

    def verify_chain(self, snapshot_id):
        """校验从全量根到 snapshot_id 的整条链，返回 [根..目标] 的 id 列表。"""
        chain = []
        current_id = snapshot_id
        child_manifest = None
        while True:
            manifest = self._load_manifest(
                current_id,
                referenced_by=child_manifest["id"] if child_manifest else None,
            )
            self._check_manifest(manifest, current_id)
            if child_manifest is not None:
                if (child_manifest.get("parent_id") != manifest.get("id")
                        or child_manifest.get("parent_hash")
                        != manifest.get("manifest_hash")):
                    raise BrokenChainError(
                        "链断裂: 快照 %s 声明的父快照引用与父快照 %s 的"
                        "实际校验值不符" % (child_manifest["id"], manifest.get("id"))
                    )
            chain.append(current_id)
            stype = manifest.get("type")
            if stype == "full":
                if manifest.get("parent_id") is not None:
                    raise BrokenChainError(
                        "链断裂: 全量快照 %s 不应有父快照" % current_id
                    )
                break
            if stype != "incremental":
                raise BrokenChainError(
                    "链断裂: 快照 %s 类型未知: %r" % (current_id, stype)
                )
            if not manifest.get("parent_id") or not manifest.get("parent_hash"):
                raise BrokenChainError(
                    "链断裂: 增量快照 %s 缺少父快照标识或父校验值" % current_id
                )
            child_manifest = manifest
            current_id = manifest["parent_id"]
        chain.reverse()
        return chain

    # ---------------- 内部：状态重建 ----------------

    def _reconstruct_state(self, snapshot_id):
        """校验整条链并重建目标时刻的 {rel: sha256} 状态。"""
        chain = self.verify_chain(snapshot_id)
        state = {}
        for sid in chain:
            manifest = self._load_manifest(sid)
            changes = manifest.get("changes", {})
            for rel in changes.get("deletes", []):
                _check_rel_path(rel)
                state.pop(rel, None)
            for rel, meta in changes.get("upserts", {}).items():
                _check_rel_path(rel)
                state[rel] = meta["sha256"]
            if _state_hash(state) != manifest.get("state_hash"):
                raise ChecksumMismatchError(
                    "校验失败: 快照 %s 重建状态与其 state_hash 不符" % sid
                )
        return state

    # ---------------- 对外：创建快照 ----------------

    def latest_id(self):
        head = os.path.join(self.path, "HEAD")
        if not os.path.exists(head):
            return None
        with open(head, "r", encoding="utf-8") as f:
            return f.read().strip() or None

    def _write_head(self, snapshot_id):
        with open(os.path.join(self.path, "HEAD"), "w", encoding="utf-8") as f:
            f.write(snapshot_id)

    def _store_object(self, src_path, sha):
        dst = os.path.join(self.objects_dir, sha)
        if os.path.exists(dst):
            return
        tmp = dst + ".tmp-" + uuid.uuid4().hex
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dst)

    def create_full(self, source_dir):
        """创建全量快照，返回快照 id。"""
        return self._create(source_dir, "full", None)

    def create_incremental(self, source_dir, parent_id=None):
        """创建增量快照；parent_id 缺省为当前最新快照。"""
        if parent_id is None:
            parent_id = self.latest_id()
            if parent_id is None:
                raise ChainError("库中还没有任何快照，请先创建全量快照")
        return self._create(source_dir, "incremental", parent_id)

    def _create(self, source_dir, stype, parent_id):
        scanned = scan_dir(source_dir)
        parent_manifest = None
        if stype == "full":
            parent_state = {}
        else:
            parent_manifest = self._load_manifest(parent_id)
            self._check_manifest(parent_manifest, parent_id)
            parent_state = self._reconstruct_state(parent_id)

        upserts = {}
        for rel, (sha, size) in scanned.items():
            if parent_state.get(rel) != sha:
                upserts[rel] = {"sha256": sha, "size": size}
                self._store_object(os.path.join(source_dir, *rel.split("/")), sha)
        deletes = sorted(set(parent_state) - set(scanned))
        new_state = {rel: sha for rel, (sha, _size) in scanned.items()}

        manifest = {
            "version": FORMAT_VERSION,
            "id": uuid.uuid4().hex,
            "type": stype,
            "parent_id": parent_id if stype == "incremental" else None,
            "parent_hash": (
                parent_manifest["manifest_hash"] if parent_manifest else None
            ),
            "created_at": time.time(),
            "changes": {"upserts": upserts, "deletes": deletes},
            "state_hash": _state_hash(new_state),
        }
        manifest["manifest_hash"] = compute_manifest_hash(manifest)

        snap_dir = os.path.join(self.snapshots_dir, manifest["id"])
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        self._write_head(manifest["id"])
        return manifest["id"]

    # ---------------- 对外：恢复 ----------------

    def restore(self, snapshot_id, dest_dir):
        """校验整条链并把 snapshot_id 时刻的状态恢复到 dest_dir。

        恢复是幂等的：dest_dir 已存在时先清空再重建，
        重复执行结果完全一致。任何链校验或数据校验失败都会抛出
        可区分的 ChainError 子类，且不会产出半成品数据
        （先全部校验数据块，再写入目标目录）。
        """
        start = time.perf_counter()
        state = self._reconstruct_state(snapshot_id)
        chain_length = len(self.verify_chain(snapshot_id))

        # 先校验所有数据块，再动目标目录，避免产出半对的数据。
        total_bytes = 0
        for rel, sha in sorted(state.items()):
            obj = os.path.join(self.objects_dir, sha)
            if not os.path.exists(obj):
                raise CorruptObjectError(
                    "数据块缺失: %s（文件 %s 需要）" % (sha, rel)
                )
            actual = _sha256_file(obj)
            if actual != sha:
                raise CorruptObjectError(
                    "数据块损坏: %s 的实际校验值为 %s（文件 %s）"
                    % (sha, actual, rel)
                )
            total_bytes += os.path.getsize(obj)

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(dest_dir)
        for rel, sha in sorted(state.items()):
            dst = os.path.join(dest_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(dst) or dest_dir, exist_ok=True)
            shutil.copyfile(os.path.join(self.objects_dir, sha), dst)

        elapsed = time.perf_counter() - start
        return RestoreReport(
            snapshot_id, chain_length, len(state), total_bytes, elapsed
        )
