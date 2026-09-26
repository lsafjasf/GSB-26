"""replica_sync —— 多副本数据比对与自愈库（仅依赖标准库）。

核心概念：
- ReplicaStore：线程安全的按块存储，每块附带存储层校验和（模拟真实存储的
  自检能力；数据位腐烂而校验和未更新时，读取即判定为“校验失败”）。
- compare_replicas：逐块比对所有副本，输出每份副本的差异清单与逐块仲裁结果。
- repair_replicas：按“严格多数派”自愈；无法形成多数时拒绝自动修复并报告分歧；
  修复写入使用 compare-and-swap，并发新写入不会被回退；修复幂等。
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter, namedtuple
from dataclasses import dataclass, field

DEFAULT_CHUNK_SIZE = 1 << 20  # 1 MiB


def hash_chunk(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# 块状态：kind ∈ {"ok", "missing", "checksum_error"}；digest 仅在 ok 时有值。
Status = namedtuple("Status", ["kind", "digest"])

OK = "ok"
MISSING = "missing"
CHECKSUM_ERROR = "checksum_error"
MISMATCH = "mismatch"  # 校验和有效，但内容与多数派不一致（落后/被篡改）


class ReplicaStore:
    """单份副本的按块存储（内存实现，线程安全）。

    - write_chunk：正常客户端写入，同时登记正确的存储层校验和。
    - corrupt_chunk：模拟位腐烂——只改数据、保留旧校验和，读取时即校验失败。
    - compare_and_swap：仅当当前内容仍等于 expected 时才写入，用于并发安全修复。
    """

    def __init__(self, name: str):
        self.name = name
        self._chunks: dict[int, bytes] = {}
        self._checksums: dict[int, str] = {}
        self._lock = threading.RLock()
        self.write_count = 0          # 累计写入次数（幂等性断言用）
        self.read_hook = None         # 测试注入点：read_chunk 读后回调

    # ---- 客户端接口 ----

    def write_chunk(self, index: int, data: bytes) -> None:
        with self._lock:
            self._chunks[index] = bytes(data)
            self._checksums[index] = hash_chunk(data)
            self.write_count += 1

    def delete_chunk(self, index: int) -> None:
        with self._lock:
            self._chunks.pop(index, None)
            self._checksums.pop(index, None)

    # ---- 故障注入（测试/演示用） ----

    def corrupt_chunk(self, index: int, data: bytes) -> None:
        """模拟位腐烂：数据被改写，但存储层校验和仍是旧值。"""
        with self._lock:
            if index not in self._chunks:
                raise KeyError(index)
            self._chunks[index] = bytes(data)

    # ---- 比对/修复接口 ----

    def read_chunk(self, index: int) -> bytes | None:
        with self._lock:
            data = self._chunks.get(index)
        if self.read_hook is not None:
            self.read_hook(self, index)
        return data

    def chunk_status(self, index: int) -> Status:
        with self._lock:
            if index not in self._chunks:
                return Status(MISSING, None)
            data = self._chunks[index]
            recorded = self._checksums.get(index)
        actual = hash_chunk(data)
        if recorded != actual:
            return Status(CHECKSUM_ERROR, None)
        return Status(OK, actual)

    def chunk_indices(self) -> set[int]:
        with self._lock:
            return set(self._chunks)

    def compare_and_swap(self, index: int, expected: bytes | None, data: bytes) -> bool:
        """仅当当前内容等于 expected（None 表示缺失）时原子写入。"""
        with self._lock:
            if self._chunks.get(index) != expected:
                return False
            self._chunks[index] = bytes(data)
            self._checksums[index] = hash_chunk(data)
            self.write_count += 1
            return True


# ---------------------------------------------------------------- 比对

@dataclass
class ChunkIssue:
    index: int
    kind: str            # missing | checksum_error | mismatch
    detail: str


@dataclass
class ChunkVerdict:
    index: int
    majority_digest: str | None      # 严格多数派的摘要；无法形成多数时为 None
    divergent: bool                  # 存在分歧且无法自动仲裁
    votes: dict[str, list[str]]      # digest -> [副本名]（仅健康票）


@dataclass
class CompareReport:
    replica_names: list[str]
    total_chunks: int
    diffs: dict[str, list[ChunkIssue]] = field(default_factory=dict)
    verdicts: dict[int, ChunkVerdict] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not any(self.diffs.values())


def _strict_majority(digests: list[str], total_replicas: int) -> str | None:
    """严格多数：得票数必须 > 副本总数的一半（缺失/校验失败不计票）。"""
    if not digests:
        return None
    digest, count = Counter(digests).most_common(1)[0]
    return digest if count > total_replicas / 2 else None


def compare_replicas(replicas: list[ReplicaStore]) -> CompareReport:
    names = [r.name for r in replicas]
    all_indices: set[int] = set()
    for r in replicas:
        all_indices |= r.chunk_indices()

    report = CompareReport(names, len(all_indices),
                           diffs={n: [] for n in names})
    for index in sorted(all_indices):
        statuses = {r.name: r.chunk_status(index) for r in replicas}
        healthy = {n: s.digest for n, s in statuses.items() if s.kind == OK}
        votes: dict[str, list[str]] = {}
        for n, d in healthy.items():
            votes.setdefault(d, []).append(n)
        majority = _strict_majority(list(healthy.values()), len(replicas))

        report.verdicts[index] = ChunkVerdict(
            index=index,
            majority_digest=majority,
            divergent=majority is None and len(votes) > 0,
            votes=votes,
        )

        for r in replicas:
            st = statuses[r.name]
            if st.kind == MISSING:
                report.diffs[r.name].append(ChunkIssue(
                    index, MISSING, "块缺失"))
            elif st.kind == CHECKSUM_ERROR:
                report.diffs[r.name].append(ChunkIssue(
                    index, CHECKSUM_ERROR, "存储层校验和与内容不符（位腐烂）"))
            elif majority is not None and st.digest != majority:
                report.diffs[r.name].append(ChunkIssue(
                    index, MISMATCH,
                    f"内容落后/不一致：本副本 {st.digest[:12]}… ≠ 多数派 {majority[:12]}…"))
            elif majority is None:
                report.diffs[r.name].append(ChunkIssue(
                    index, MISMATCH,
                    f"无法形成多数派，本副本摘要 {st.digest[:12]}…"))
    return report


def format_report(report: CompareReport) -> str:
    lines = [f"=== 副本差异报告 ===",
             f"副本数: {len(report.replica_names)}，总块数: {report.total_chunks}"]
    for index in sorted(report.verdicts):
        v = report.verdicts[index]
        if v.majority_digest is not None and not any(
                i.index == index for issues in report.diffs.values() for i in issues):
            continue  # 全员一致，不展开
        if v.majority_digest is not None:
            holders = "、".join(v.votes.get(v.majority_digest, []))
            lines.append(f"块 #{index}: 多数派 {v.majority_digest[:12]}…（{holders}）")
        else:
            ballot = "；".join(f"{d[:12]}… <- [{'、'.join(ns)}]" for d, ns in v.votes.items())
            lines.append(f"块 #{index}: 【分歧】无法形成多数派：{ballot or '全部缺失'}")
        for name in report.replica_names:
            for issue in report.diffs[name]:
                if issue.index == index:
                    lines.append(f"  - {name}: [{issue.kind}] {issue.detail}")
    lines.append("--- 每副本差异清单 ---")
    for name in report.replica_names:
        issues = report.diffs[name]
        if not issues:
            lines.append(f"{name}: 无差异")
        else:
            lines.append(f"{name}: {len(issues)} 处差异")
            for i in issues:
                lines.append(f"  块 #{i.index} [{i.kind}] {i.detail}")
    if report.clean:
        lines.append("结论：全部副本一致。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 修复

@dataclass
class RepairReport:
    repaired: list[tuple[str, int]] = field(default_factory=list)    # (副本, 块)
    conflicts: list[tuple[str, int]] = field(default_factory=list)   # 并发写入冲突，未动
    divergences: list[int] = field(default_factory=list)             # 无多数派，拒绝修复

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.divergences


def repair_replicas(replicas: list[ReplicaStore],
                    report: CompareReport | None = None,
                    max_attempts: int = 8) -> RepairReport:
    """按多数派自愈。

    策略：
    1. 逐块取健康副本的严格多数派值；无多数派 -> 记入 divergences，拒绝自动修复。
    2. 只修复确有问题的块（缺失/校验失败/与多数派不一致），健康块绝不写入。
    3. 写入一律 compare-and-swap：若读后被并发写入改动，则放弃该 (副本, 块)，
       记入 conflicts 且本轮不再触碰 —— 并发新写入绝不被回退。
    4. 幂等：全部收敛后再次执行不会产生任何写入。
    """
    if report is None:
        report = compare_replicas(replicas)
    by_name = {r.name: r for r in replicas}
    result = RepairReport()
    conflicted: set[tuple[str, int]] = set()

    for index in sorted(report.verdicts):
        for _attempt in range(max_attempts):
            statuses = {r.name: r.chunk_status(index) for r in replicas}
            healthy = {n: s.digest for n, s in statuses.items() if s.kind == OK}
            majority = _strict_majority(list(healthy.values()), len(replicas))
            if majority is None:
                if index not in result.divergences:
                    result.divergences.append(index)
                break
            targets = [n for n in by_name
                       if (statuses[n].kind != OK or statuses[n].digest != majority)
                       and (n, index) not in conflicted]
            if not targets:
                break  # 该块已收敛（或仅剩冲突项）
            source = next(n for n, d in healthy.items() if d == majority)
            data = by_name[source].read_chunk(index)
            cas_failed = False
            for n in targets:
                store = by_name[n]
                current = store.read_chunk(index)  # 可能为 None（缺失）或腐烂数据
                if store.compare_and_swap(index, current, data):
                    if (n, index) not in result.repaired:
                        result.repaired.append((n, index))
                else:
                    # 并发写入介入：绝不覆盖，标记冲突，本轮不再触碰该块
                    conflicted.add((n, index))
                    if (n, index) not in result.conflicts:
                        result.conflicts.append((n, index))
                    cas_failed = True
            if not cas_failed:
                break  # 无竞争，一次到位；有竞争则重估多数派后再收敛
    result.repaired.sort()
    result.conflicts.sort()
    result.divergences.sort()
    return result


def format_repair_report(report: RepairReport) -> str:
    lines = ["=== 修复报告 ==="]
    if report.repaired:
        lines.append(f"已修复 {len(report.repaired)} 处：")
        for name, index in report.repaired:
            lines.append(f"  {name} 块 #{index} <- 多数派值")
    else:
        lines.append("无需修复（0 处写入）。")
    for name, index in report.conflicts:
        lines.append(f"冲突：{name} 块 #{index} 修复期间被并发写入，已保留新写入，未回退。")
    for index in report.divergences:
        lines.append(f"分歧：块 #{index} 无法形成多数派，拒绝自动修复，需人工仲裁。")
    return "\n".join(lines)
