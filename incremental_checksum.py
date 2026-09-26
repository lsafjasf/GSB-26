"""增量数据校验库（仅标准库）。

模型：
- ShardStore 模拟存储层：保存分片数据、每分片版本号、写入时摘要。
  * write_shard  : 合法写入，版本号 +1，并记录写入时摘要（端到端基准）。
  * tamper_shard : 旁路篡改（磁盘损坏/恶意改写），只改数据，不动版本号。
- ShardVerifier 维护清单 manifest：{分片号 -> (摘要, 版本)}。
- verify() 增量校验：
  * 版本号与清单不一致的分片 = 变化分片，强制重算并与写入时摘要比对；
  * 版本一致的分片 = 未变化分片，按 sample_ratio 抽样复核，与清单摘要比对；
  * 未抽中的分片不读取、不哈希，记为「未覆盖」——这就是省下的成本，
    也是漏检风险的来源（旁路篡改不动版本号，只能靠抽样发现）。
- 结论三态：PASS / MISMATCH / UNCOVERED，未覆盖绝不算通过。
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

PASS = "PASS"            # 通过：所有分片均被检查且一致
MISMATCH = "MISMATCH"    # 发现不一致：至少一个分片校验失败
UNCOVERED = "UNCOVERED"  # 未覆盖：无不一致，但存在未被检查的分片


def _digest(chunk: bytes, hash_name: str = "sha256") -> str:
    return hashlib.new(hash_name, chunk).hexdigest()


class ShardStore:
    """存储层抽象：分片数据 + 版本号 + 写入时摘要。"""

    def __init__(self, shard_size: int = 4096, hash_name: str = "sha256"):
        if shard_size <= 0:
            raise ValueError("shard_size 必须为正整数")
        self.shard_size = shard_size
        self.hash_name = hash_name
        self._chunks: list[bytes] = []
        self.versions: list[int] = []
        self._write_digests: list[str] = []

    @classmethod
    def from_bytes(cls, data: bytes, shard_size: int = 4096,
                   hash_name: str = "sha256") -> "ShardStore":
        store = cls(shard_size, hash_name)
        for i in range(0, len(data), shard_size):
            store.append_shard(data[i:i + shard_size])
        return store

    def __len__(self) -> int:
        return len(self._chunks)

    def append_shard(self, chunk: bytes) -> None:
        if not 1 <= len(chunk) <= self.shard_size:
            raise ValueError("分片大小必须在 1..shard_size 之间")
        self._chunks.append(bytes(chunk))
        self.versions.append(1)
        self._write_digests.append(_digest(chunk, self.hash_name))

    def write_shard(self, index: int, chunk: bytes) -> None:
        """合法写入：更新数据、版本 +1、刷新写入时摘要。"""
        if not 1 <= len(chunk) <= self.shard_size:
            raise ValueError("分片大小必须在 1..shard_size 之间")
        self._chunks[index] = bytes(chunk)
        self.versions[index] += 1
        self._write_digests[index] = _digest(chunk, self.hash_name)

    def tamper_shard(self, index: int, chunk: bytes) -> None:
        """旁路篡改：只改数据，不动版本号与写入时摘要（校验要发现的就是它）。"""
        if not 1 <= len(chunk) <= self.shard_size:
            raise ValueError("分片大小必须在 1..shard_size 之间")
        self._chunks[index] = bytes(chunk)

    def truncate(self, n: int) -> None:
        del self._chunks[n:]
        del self.versions[n:]
        del self._write_digests[n:]

    def read_shard(self, index: int) -> bytes:
        return self._chunks[index]

    def write_digest(self, index: int) -> str:
        return self._write_digests[index]


@dataclass
class ShardRecord:
    digest: str
    version: int


@dataclass
class VerifyReport:
    conclusion: str
    total_shards: int
    changed_shards: int            # 变化分片数（强制重算）
    sampled_unchanged: int         # 未变化分片中被抽样复核的数量
    checked_shards: int            # 本次实际检查的分片总数
    uncovered_shards: int          # 未覆盖分片数
    coverage: float                # 覆盖率 = checked / total
    mismatches: list[int] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"结论={self.conclusion} 总分片={self.total_shards} "
            f"变化分片={self.changed_shards} 抽样复核={self.sampled_unchanged} "
            f"实际检查={self.checked_shards} 未覆盖={self.uncovered_shards} "
            f"覆盖率={self.coverage:.2%} 不一致分片={self.mismatches}"
        )


class ShardVerifier:
    """按分片维护校验信息，支持增量校验与抽样复核。"""

    def __init__(self, store: ShardStore):
        self.store = store
        self.manifest: dict[int, ShardRecord] = {}

    def commit(self) -> None:
        """全量重算并接受当前数据为新基线（初始化或确认无误后调用）。"""
        self.manifest = {
            i: ShardRecord(digest=_digest(self.store.read_shard(i),
                                          self.store.hash_name),
                           version=self.store.versions[i])
            for i in range(len(self.store))
        }

    def verify(self, sample_ratio: float = 0.0,
               seed: int | None = None) -> VerifyReport:
        """增量校验：变化分片必查，未变化分片按 sample_ratio 抽样复核。"""
        if not 0.0 <= sample_ratio <= 1.0:
            raise ValueError("sample_ratio 必须在 [0, 1] 内")
        rng = random.Random(seed)
        total = len(self.store)
        changed = sampled = 0
        mismatches: list[int] = []

        for i in range(total):
            rec = self.manifest.get(i)
            if rec is None or rec.version != self.store.versions[i]:
                # 变化分片（含新增分片）：强制重算，与写入时摘要比对
                changed += 1
                if _digest(self.store.read_shard(i),
                           self.store.hash_name) != self.store.write_digest(i):
                    mismatches.append(i)
            elif rng.random() < sample_ratio:
                # 未变化分片：抽中则复核，与清单摘要比对
                sampled += 1
                if _digest(self.store.read_shard(i),
                           self.store.hash_name) != rec.digest:
                    mismatches.append(i)
            # 未抽中：不读取不哈希，计入未覆盖

        # 数据缩短：丢失的分片视为不一致
        for i in range(total, max(self.manifest) + 1 if self.manifest else 0):
            if i in self.manifest:
                mismatches.append(i)

        checked = changed + sampled
        uncovered = total - checked
        coverage = (checked / total) if total else 1.0
        if mismatches:
            conclusion = MISMATCH
        elif uncovered > 0:
            conclusion = UNCOVERED
        else:
            conclusion = PASS
        return VerifyReport(
            conclusion=conclusion, total_shards=total,
            changed_shards=changed, sampled_unchanged=sampled,
            checked_shards=checked, uncovered_shards=uncovered,
            coverage=coverage, mismatches=sorted(mismatches),
        )
