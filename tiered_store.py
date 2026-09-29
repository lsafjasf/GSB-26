"""冷热分层的内存存储（仅依赖标准库）。

热层：内存 dict，容量按字节限制。
冷层：磁盘目录，每个 key 一个文件。

冷热判定依据（可配置权重）：
    score(key) = freq_weight * freq(key) + recency_weight / (1 + age(key))
  - freq(key)：访问频次，带指数衰减（每 decay_interval 次访问整体乘以 decay_factor），
    使突发热点能被快速识别，同时让过气热点逐渐冷却。
  - age(key)：距上次访问的 tick 数（最近访问时间）。

迁移时机：
  - 下沉（热 -> 冷）：写入后热层字节数超容量时，按 score 最低者逐个下沉；
    开启 early_sink 时，每次写入后主动把 score 低于 sink_score_threshold 的数据提前下沉。
  - 上浮（冷 -> 热）：读命中冷层时立即上浮（可能触发连锁下沉）；
    开启 prefetch 时，顺带把 key 排序上相邻的 prefetch_count 个冷数据一起上浮。
"""

from __future__ import annotations

import bisect
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

_MISSING = object()


@dataclass
class Metrics:
    hot_hits: int = 0          # 读命中热层
    cold_hits: int = 0         # 读命中冷层（缓存未命中，需磁盘读）
    misses: int = 0            # key 不存在
    disk_reads: int = 0        # 磁盘读次数（含预取）
    disk_writes: int = 0       # 磁盘写次数（下沉 + 超限直写）
    disk_read_bytes: int = 0
    disk_write_bytes: int = 0
    read_ops: int = 0
    read_latency_sum: float = 0.0  # 秒

    @property
    def total_reads(self) -> int:
        return self.hot_hits + self.cold_hits + self.misses

    @property
    def hit_rate(self) -> float:
        """热层命中率。"""
        total = self.total_reads
        return self.hot_hits / total if total else 0.0

    @property
    def avg_read_latency(self) -> float:
        """平均读延迟（秒）。"""
        return self.read_latency_sum / self.read_ops if self.read_ops else 0.0

    def snapshot(self) -> dict:
        return {
            "hot_hits": self.hot_hits,
            "cold_hits": self.cold_hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "avg_read_latency_us": round(self.avg_read_latency * 1e6, 2),
            "disk_reads": self.disk_reads,
            "disk_writes": self.disk_writes,
            "disk_read_bytes": self.disk_read_bytes,
            "disk_write_bytes": self.disk_write_bytes,
        }


class _Meta:
    __slots__ = ("last_access", "size")

    def __init__(self, last_access: int, size: int):
        self.last_access = last_access
        self.size = size


class TieredStore:
    def __init__(
        self,
        capacity_bytes: int,
        cold_dir: str | None = None,
        *,
        freq_weight: float = 1.0,
        recency_weight: float = 1.0,
        decay_interval: int = 1000,
        decay_factor: float = 0.5,
        prefetch: bool = False,
        prefetch_count: int = 4,
        early_sink: bool = False,
        sink_score_threshold: float = 0.5,
    ):
        if capacity_bytes < 0:
            raise ValueError("capacity_bytes must be >= 0")
        self.capacity = capacity_bytes
        self.freq_weight = freq_weight
        self.recency_weight = recency_weight
        self.decay_interval = max(1, decay_interval)
        self.decay_factor = decay_factor
        self.prefetch = prefetch
        self.prefetch_count = max(0, prefetch_count)
        self.early_sink = early_sink
        self.sink_score_threshold = sink_score_threshold

        self._hot: dict[str, bytes] = {}
        self._meta: dict[str, _Meta] = {}
        self._hot_bytes = 0
        self._cold: set[str] = set()
        self._freq: dict[str, float] = {}
        self._tick = 0

        self.metrics = Metrics()

        self._owns_dir = cold_dir is None
        self._cold_dir = cold_dir or tempfile.mkdtemp(prefix="tiered_store_")
        os.makedirs(self._cold_dir, exist_ok=True)

    # ------------------------------------------------------------------ 基础

    def _path(self, key: str) -> str:
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self._cold_dir, name)

    def _touch(self, key: str) -> None:
        self._tick += 1
        self._freq[key] = self._freq.get(key, 0.0) + 1.0
        if self._tick % self.decay_interval == 0:
            for k in self._freq:
                self._freq[k] *= self.decay_factor

    def _score(self, key: str) -> float:
        meta = self._meta[key]
        age = self._tick - meta.last_access
        return (
            self.freq_weight * self._freq.get(key, 0.0)
            + self.recency_weight / (1.0 + age)
        )

    # ------------------------------------------------------------------ 迁移

    def _write_cold(self, key: str, value: bytes) -> None:
        with open(self._path(key), "wb") as f:
            f.write(value)
        self._cold.add(key)
        self.metrics.disk_writes += 1
        self.metrics.disk_write_bytes += len(value)

    def _read_cold(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            value = f.read()
        self.metrics.disk_reads += 1
        self.metrics.disk_read_bytes += len(value)
        return value

    def _remove_cold(self, key: str) -> None:
        if key in self._cold:
            self._cold.discard(key)
            try:
                os.unlink(self._path(key))
            except FileNotFoundError:
                pass

    def _demote(self, key: str) -> None:
        """下沉：热 -> 冷。"""
        value = self._hot.pop(key)
        meta = self._meta.pop(key)
        self._hot_bytes -= meta.size
        self._write_cold(key, value)

    def _promote(self, key: str, value: bytes) -> None:
        """上浮：冷 -> 热。单条大于容量时不上浮（保留在磁盘）。"""
        if len(value) > self.capacity:
            return
        self._remove_cold(key)
        self._hot[key] = value
        self._meta[key] = _Meta(self._tick, len(value))
        self._hot_bytes += len(value)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while self._hot_bytes > self.capacity and self._hot:
            victim = min(self._hot, key=self._score)
            self._demote(victim)

    def _maybe_sink(self, protect: str) -> None:
        """提前下沉：把低分热数据主动写到磁盘，换取内存余量。"""
        while self._hot:
            victim = min(self._hot, key=self._score)
            if victim == protect or self._score(victim) >= self.sink_score_threshold:
                return
            self._demote(victim)

    def _prefetch(self, key: str) -> None:
        """预取：把排序上相邻的冷数据提前上浮。"""
        if not self._cold:
            return
        keys = sorted(self._cold)
        idx = bisect.bisect_right(keys, key)
        for nxt in keys[idx : idx + self.prefetch_count]:
            self._promote(nxt, self._read_cold(nxt))

    # ------------------------------------------------------------------ API

    def put(self, key: str, value: bytes) -> None:
        value = bytes(value)
        self._touch(key)
        if key in self._hot:
            self._hot_bytes -= self._meta[key].size
            self._hot[key] = value
            self._meta[key] = _Meta(self._tick, len(value))
            self._hot_bytes += len(value)
        else:
            self._remove_cold(key)  # 覆盖冷层旧值，避免读到过期数据
            if len(value) <= self.capacity:
                self._hot[key] = value
                self._meta[key] = _Meta(self._tick, len(value))
                self._hot_bytes += len(value)
            else:
                self._write_cold(key, value)  # 单条超容量：直接落盘
        self._evict_if_needed()
        if self.early_sink:
            self._maybe_sink(protect=key)

    def _get_inner(self, key: str, default):
        if key in self._hot:
            self._touch(key)  # 频次更新只针对真实命中，避免不存在的键污染频次表
            self._meta[key].last_access = self._tick
            self.metrics.hot_hits += 1
            return self._hot[key]
        if key in self._cold:
            self._touch(key)  # miss 既不记录频次也不推进 tick，防止无效查询带偏冷热判定
            self.metrics.cold_hits += 1
            value = self._read_cold(key)
            self._promote(key, value)
            if self.prefetch:
                self._prefetch(key)
            return value
        self.metrics.misses += 1
        return default

    def get(self, key: str, default=None):
        start = time.perf_counter()
        try:
            return self._get_inner(key, default)
        finally:
            self.metrics.read_ops += 1
            self.metrics.read_latency_sum += time.perf_counter() - start

    def __getitem__(self, key: str) -> bytes:
        start = time.perf_counter()
        try:
            value = self._get_inner(key, _MISSING)
        finally:
            self.metrics.read_ops += 1
            self.metrics.read_latency_sum += time.perf_counter() - start
        if value is _MISSING:
            raise KeyError(key)
        return value

    def delete(self, key: str) -> None:
        if key in self._hot:
            meta = self._meta.pop(key)
            self._hot_bytes -= meta.size
            del self._hot[key]
        self._remove_cold(key)
        self._freq.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._hot or key in self._cold

    def __len__(self) -> int:
        return len(self._hot) + len(self._cold)

    def keys(self):
        return list(self._hot) + list(self._cold)

    def hot_keys(self):
        return list(self._hot)

    def cold_keys(self):
        return list(self._cold)

    @property
    def hot_bytes(self) -> int:
        return self._hot_bytes

    def close(self) -> None:
        if self._owns_dir:
            shutil.rmtree(self._cold_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
