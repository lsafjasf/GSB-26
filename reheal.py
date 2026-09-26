"""多副本数据比对与自愈库（仅使用 Python 标准库）。

概念：
- 副本（replica）：一个普通文件，内容为同一数据的按块序列。
- 块（chunk）：固定大小（默认 1 MiB）的分片，最后一块可以不足。
- 清单（manifest）：可选的 sidecar 文件 ``<replica>.manifest.json``，
  记录每个块期望的 SHA-256，用于识别“校验失败块”。

修复策略（详见 README.md）：
- 某块若有多数派（同意副本数 > 副本总数/2），少数派按多数派修复。
- 校验失败块在投票中弃权；若所有可信投票一致，则视为多数派。
- 无法形成多数派（如两副本互相矛盾、三方各执一词）时拒绝自动修复，
  并在报告中列出分歧块。
- 修复采用 compare-and-swap：写入前重新读取目标块，若与扫描时不一致
  （说明期间有新写入），则跳过该块并记录，绝不回退并发写入。
- 修复幂等：已符合多数派的块直接跳过，重复执行不产生额外改动。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
from collections import Counter
from dataclasses import dataclass, field

DEFAULT_CHUNK_SIZE = 1 << 20  # 1 MiB


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 并发安全的基础读写
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    key = os.path.abspath(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _read_chunk_unlocked(path: str, index: int, chunk_size: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(index * chunk_size)
        return fh.read(chunk_size)


def read_chunk(path: str, index: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> bytes:
    """读取一个块；文件不够长时返回短数据或空 bytes。"""
    with _lock_for(path):
        return _read_chunk_unlocked(path, index, chunk_size)


def write_chunk(path: str, index: int, data: bytes,
                chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
    """并发安全的块写入，供业务写入线程与修复器共用同一把锁。

    注意：本函数只覆盖写入 ``len(data)`` 个字节，不改变文件长度；
    写最后一个块时如需缩短文件，调用方应自行截断。
    """
    if len(data) > chunk_size:
        raise ValueError("chunk data larger than chunk_size")
    with _lock_for(path):
        with open(path, "r+b") as fh:
            fh.seek(index * chunk_size)
            fh.write(data)


# ---------------------------------------------------------------------------
# 清单（manifest）
# ---------------------------------------------------------------------------

def manifest_path(replica_path: str) -> str:
    return replica_path + ".manifest.json"


def load_manifest(replica_path: str) -> dict[int, str] | None:
    mpath = manifest_path(replica_path)
    if not os.path.exists(mpath):
        return None
    with open(mpath, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {int(k): v for k, v in raw.items()}


def save_manifest(replica_path: str, manifest: dict[int, str]) -> None:
    with open(manifest_path(replica_path), "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in sorted(manifest.items())}, fh, indent=2)


def build_manifest(replica_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[int, str]:
    """根据当前文件内容生成并落盘一份清单。"""
    manifest = {i: h for i, h in enumerate(_hash_file(replica_path, chunk_size))}
    save_manifest(replica_path, manifest)
    return manifest


def _hash_file(path: str, chunk_size: int) -> list[str]:
    hashes = []
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            hashes.append(_sha256(block))
    return hashes


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------

@dataclass
class ReplicaDiff:
    path: str
    missing: list[int] = field(default_factory=list)           # 缺失块
    mismatched: list[int] = field(default_factory=list)        # 与多数派内容不一致块
    checksum_failed: list[int] = field(default_factory=list)   # 校验失败块
    extra: list[int] = field(default_factory=list)             # 多数派认为不应存在的块

    def clean(self) -> bool:
        return not (self.missing or self.mismatched
                    or self.checksum_failed or self.extra)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "missing": self.missing,
            "mismatched": self.mismatched,
            "checksum_failed": self.checksum_failed,
            "extra": self.extra,
        }


@dataclass
class DiffReport:
    chunk_size: int
    chunk_count: int
    replicas: list[ReplicaDiff]
    divergent: list[int]  # 无法形成多数派的块

    # 以下为内部状态，供修复阶段复用，不进入 JSON 报告
    hashes: list[list[str]] = field(default_factory=list, repr=False)
    sizes: list[int] = field(default_factory=list, repr=False)
    manifests: list[dict[int, str] | None] = field(default_factory=list, repr=False)
    majority: dict[int, str] = field(default_factory=dict, repr=False)
    target_size: int | None = field(default=None, repr=False)

    @property
    def consistent(self) -> bool:
        return not self.divergent and all(d.clean() for d in self.replicas)

    def to_dict(self) -> dict:
        return {
            "chunk_size": self.chunk_size,
            "chunk_count": self.chunk_count,
            "replica_count": len(self.replicas),
            "consistent": self.consistent,
            "divergent": self.divergent,
            "replicas": [d.to_dict() for d in self.replicas],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def scan_replicas(paths: list[str],
                  chunk_size: int = DEFAULT_CHUNK_SIZE,
                  manifests: list[dict[int, str] | None] | None = None) -> DiffReport:
    """按块比对各副本，输出每份副本的差异清单。

    多数派判定：同意某块内容的副本数 > 副本总数 / 2。
    校验失败块弃权投票；若剩余可信投票完全一致，也视为多数派。
    """
    n = len(paths)
    if n == 0:
        raise ValueError("need at least one replica")
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)

    if manifests is None:
        manifests = [load_manifest(p) for p in paths]

    hashes = [_hash_file(p, chunk_size) for p in paths]
    sizes = [os.path.getsize(p) for p in paths]
    chunk_count = max((len(h) for h in hashes), default=0)

    diffs = [ReplicaDiff(path=p) for p in paths]
    divergent: list[int] = []
    majority: dict[int, str] = {}

    for c in range(chunk_count):
        present = [r for r in range(n) if c < len(hashes[r])]
        if len(present) <= n / 2:
            # 多数副本都没有这一块：拥有它的副本属于“多出块”
            for r in present:
                diffs[r].extra.append(c)
            continue

        failed = [r for r in present
                  if manifests[r] is not None
                  and manifests[r].get(c) is not None
                  and manifests[r][c] != hashes[r][c]]
        for r in failed:
            diffs[r].checksum_failed.append(c)

        voters = [r for r in present if r not in failed]
        counts = Counter(hashes[r][c] for r in voters)
        top = None
        if counts:
            h, cnt = counts.most_common(1)[0]
            if cnt > n / 2 or len(counts) == 1:
                top = h
        if top is None:
            divergent.append(c)
            continue

        majority[c] = top
        for r in range(n):
            if c >= len(hashes[r]):
                diffs[r].missing.append(c)
            elif hashes[r][c] != top and r not in failed:
                diffs[r].mismatched.append(c)

    # 目标长度：最后一个有多数派的块决定文件应有的大小
    target_size = None
    if majority:
        last = max(majority)
        donor = next(r for r in range(n)
                     if last < len(hashes[r]) and hashes[r][last] == majority[last])
        tail = sizes[donor] - last * chunk_size
        target_size = last * chunk_size + tail

    return DiffReport(
        chunk_size=chunk_size,
        chunk_count=chunk_count,
        replicas=diffs,
        divergent=divergent,
        hashes=hashes,
        sizes=sizes,
        manifests=list(manifests),
        majority=majority,
        target_size=target_size,
    )


# ---------------------------------------------------------------------------
# 修复
# ---------------------------------------------------------------------------

@dataclass
class RepairReport:
    applied: dict[str, list[int]] = field(default_factory=dict)            # 已修复块
    truncated: dict[str, int] = field(default_factory=dict)                # 截断后的新长度
    manifests_updated: dict[str, list[int]] = field(default_factory=dict)  # 清单被更新的块
    skipped_concurrent: dict[str, list[int]] = field(default_factory=dict) # 因并发写入被跳过的块
    divergent: list[int] = field(default_factory=list)                     # 分歧块（未动）
    refused: bool = False                                                  # 是否存在分歧导致部分拒绝

    def to_dict(self) -> dict:
        return {
            "applied": {k: v for k, v in sorted(self.applied.items())},
            "truncated": self.truncated,
            "manifests_updated": self.manifests_updated,
            "skipped_concurrent": self.skipped_concurrent,
            "divergent": self.divergent,
            "refused": self.refused,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def _cas_write_chunk(path: str, index: int, content: bytes,
                     expected_hash: str | None, majority_hash: str,
                     chunk_size: int) -> str:
    """Compare-and-swap 写块。

    返回 applied / already / conflict：
    - 当前内容已是多数派 -> already（幂等，无改动）
    - 当前内容与扫描时不一致（期间有新写入）-> conflict，绝不覆盖
    - 否则写入多数派内容并校验 -> applied
    """
    with _lock_for(path):
        current = _read_chunk_unlocked(path, index, chunk_size)
        current_hash = _sha256(current) if current else None
        if current_hash == majority_hash:
            return "already"
        if current_hash != expected_hash:
            return "conflict"
        with open(path, "r+b") as fh:
            fh.seek(index * chunk_size)
            fh.write(content)
        verify = _read_chunk_unlocked(path, index, chunk_size)
        if _sha256(verify) != majority_hash:
            return "conflict"
        return "applied"


def repair_replicas(paths: list[str],
                    chunk_size: int = DEFAULT_CHUNK_SIZE,
                    manifests: list[dict[int, str] | None] | None = None,
                    scan: DiffReport | None = None) -> RepairReport:
    """按多数派策略修复各副本。

    只改动确有问题的块：缺失块、与多数派不一致块、校验失败块、
    以及多数派认为不应存在的尾部块（截断）。分歧块一律不动。
    """
    if scan is None:
        scan = scan_replicas(paths, chunk_size, manifests)
    n = len(paths)
    report = RepairReport(divergent=list(scan.divergent),
                          refused=bool(scan.divergent))

    for c, maj_hash in sorted(scan.majority.items()):
        donor = next(r for r in range(n)
                     if c < len(scan.hashes[r]) and scan.hashes[r][c] == maj_hash)
        content = read_chunk(paths[donor], c, chunk_size)

        for r in range(n):
            scanned_hash = scan.hashes[r][c] if c < len(scan.hashes[r]) else None
            need_write = scanned_hash != maj_hash
            manifest = scan.manifests[r]
            need_manifest = (manifest is not None
                             and manifest.get(c) != maj_hash)
            if not need_write and not need_manifest:
                continue

            if need_write:
                outcome = _cas_write_chunk(paths[r], c, content,
                                           scanned_hash, maj_hash, chunk_size)
                if outcome == "conflict":
                    report.skipped_concurrent.setdefault(paths[r], []).append(c)
                    continue
                if outcome == "applied":
                    report.applied.setdefault(paths[r], []).append(c)

            if need_manifest:
                manifest[c] = maj_hash
                save_manifest(paths[r], manifest)
                report.manifests_updated.setdefault(paths[r], []).append(c)

    # 截断：仅当尾部所有多余块都被多数派判定为“不应存在”时才截断，
    # 分歧块绝不截掉。截断前校验文件长度与扫描时一致（CAS）。
    if scan.target_size is not None:
        first_target_chunk = -(-scan.target_size // chunk_size)  # ceil
        for r in range(n):
            extra = [c for c in scan.replicas[r].extra
                     if c >= first_target_chunk]
            beyond = set(range(first_target_chunk, len(scan.hashes[r])))
            if not beyond or not extra or beyond != set(extra):
                continue
            if scan.sizes[r] == scan.target_size:
                continue
            with _lock_for(paths[r]):
                if os.path.getsize(paths[r]) != scan.sizes[r]:
                    report.skipped_concurrent.setdefault(paths[r], []).extend(
                        sorted(beyond))
                    continue
                with open(paths[r], "r+b") as fh:
                    fh.truncate(scan.target_size)
            report.truncated[paths[r]] = scan.target_size

    return report


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reheal", description="多副本比对与自愈")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "repair"):
        sp = sub.add_parser(name)
        sp.add_argument("replicas", nargs="+", help="副本文件路径")
        sp.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        report = scan_replicas(args.replicas, args.chunk_size)
        print(report.to_json())
        return 0 if report.consistent else 1
    report = repair_replicas(args.replicas, args.chunk_size)
    print(report.to_json())
    return 2 if report.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
