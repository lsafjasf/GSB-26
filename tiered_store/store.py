"""冷热分层内存存储。

热层: 内存 dict, 容量按字节限制。
冷层: 磁盘目录, 每个 key 一个文件 (JSON: key + base64 value)。

淘汰/上浮判定依据:
  每条数据维护 freq (衰减频次) 与 last_access (逻辑时钟)。
  策略可配置:
    - "lru":    仅看最近访问时间
    - "lfu":    仅看访问频次
    - "hybrid": 归一化频次与归一化新近度的加权和 (权重可配)
  频次按 decay_interval 次操作做指数衰减, 使突发热点能快速压过历史热点。

迁移时机:
  - 下沉 (demote): 写入/上浮导致热层字节数超过容量时, 按评分最低者下沉;
    开启 eager_demote 后, 超过 demote_threshold * capacity 即提前下沉。
  - 上浮 (promote): 读命中冷层时立即上浮 (读路径保证正确性优先)。
  - 预取 (prefetch): 上浮某个 key 时, 顺带把冷层评分最高的若干 key 一起上浮。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass


@dataclass
class _Meta:
    size: int
    freq: float = 1.0
    last_access: int = 0


@dataclass
class Metrics:
    reads: int = 0
    hot_hits: int = 0
    cold_hits: int = 0
    misses: int = 0
    disk_read_ops: int = 0
    disk_read_bytes: int = 0
    disk_write_ops: int = 0
    disk_write_bytes: int = 0
    read_latency_ns: int = 0
    hot_latency_ns: int = 0
    cold_latency_ns: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hot_hits + self.cold_hits
        return self.hot_hits / total if total else 0.0

    @property
    def avg_read_latency_us(self) -> float:
        return self.read_latency_ns / 1000.0 / self.reads if self.reads else 0.0

    def snapshot(self) -> dict:
        return {
            "reads": self.reads,
            "hot_hits": self.hot_hits,
            "cold_hits": self.cold_hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "avg_read_latency_us": round(self.avg_read_latency_us, 2),
            "disk_read_ops": self.disk_read_ops,
            "disk_read_bytes": self.disk_read_bytes,
            "disk_write_ops": self.disk_write_ops,
            "disk_write_bytes": self.disk_write_bytes,
        }


class TieredStore:
    def __init__(
        self,
        capacity_bytes: int,
        cold_dir: str,
        policy: str = "hybrid",
        freq_weight: float = 1.0,
        recency_weight: float = 1.0,
        prefetch: bool = False,
        prefetch_count: int = 4,
        eager_demote: bool = False,
        demote_threshold: float = 0.9,
        decay_interval: int = 512,
        decay_factor: float = 0.5,
    ):
        if policy not in ("lru", "lfu", "hybrid"):
            raise ValueError(f"unknown policy: {policy}")
        if capacity_bytes < 0:
            raise ValueError("capacity_bytes must be >= 0")
        self.capacity = capacity_bytes
        self.cold_dir = cold_dir
        self.policy = policy
        self.freq_weight = freq_weight
        self.recency_weight = recency_weight
        self.prefetch = prefetch
        self.prefetch_count = prefetch_count
        self.eager_demote = eager_demote
        self.demote_threshold = demote_threshold
        self.decay_interval = decay_interval
        self.decay_factor = decay_factor

        os.makedirs(cold_dir, exist_ok=True)

        self._hot: dict[str, bytes] = {}
        self._meta: dict[str, _Meta] = {}  # 热层与冷层共用元数据 (元数据常驻内存)
        self._cold: set[str] = set()
        self._hot_bytes = 0
        self._clock = 0
        self._ops_since_decay = 0
        self._max_freq = 1.0
        self.metrics = Metrics()

    # ---------- 内部工具 ----------

    def _path(self, key: str) -> str:
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cold_dir, name + ".json")

    def _tick(self) -> None:
        self._clock += 1
        self._ops_since_decay += 1
        if self._ops_since_decay >= self.decay_interval:
            self._ops_since_decay = 0
            for m in self._meta.values():
                m.freq = max(1.0, m.freq * self.decay_factor)
            self._max_freq = max(1.0, self._max_freq * self.decay_factor)

    def _touch(self, key: str) -> None:
        m = self._meta[key]
        m.freq += 1.0
        m.last_access = self._clock
        if m.freq > self._max_freq:
            self._max_freq = m.freq

    def _score(self, key: str) -> float:
        m = self._meta[key]
        if self.policy == "lru":
            return float(m.last_access)
        if self.policy == "lfu":
            return m.freq + m.last_access * 1e-9
        clock = self._clock or 1
        return (
            self.freq_weight * (m.freq / self._max_freq)
            + self.recency_weight * (m.last_access / clock)
        )

    def _victim(self, exclude: set[str] | None = None) -> str | None:
        best, best_score = None, None
        for k in self._hot:
            if exclude and k in exclude:
                continue
            s = self._score(k)
            if best is None or s < best_score:
                best, best_score = k, s
        return best

    def _write_cold(self, key: str, value: bytes) -> None:
        payload = json.dumps(
            {"key": key, "value": base64.b64encode(value).decode("ascii")}
        ).encode("utf-8")
        with open(self._path(key), "wb") as f:
            f.write(payload)
        self.metrics.disk_write_ops += 1
        self.metrics.disk_write_bytes += len(payload)

    def _read_cold(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            payload = f.read()
        self.metrics.disk_read_ops += 1
        self.metrics.disk_read_bytes += len(payload)
        obj = json.loads(payload.decode("utf-8"))
        if obj["key"] != key:
            raise IOError(f"cold file corrupted for key {key!r}")
        return base64.b64decode(obj["value"])

    def _demote(self, key: str) -> None:
        value = self._hot.pop(key)
        self._hot_bytes -= self._meta[key].size
        self._write_cold(key, value)
        self._cold.add(key)

    def _promote(self, key: str) -> bytes:
        value = self._read_cold(key)
        os.unlink(self._path(key))
        self._cold.discard(key)
        self._insert_hot(key, value)
        return value

    def _insert_hot(self, key: str, value: bytes) -> None:
        size = len(value)
        old_freq = self._meta[key].freq if key in self._meta else 1.0
        self._meta[key] = _Meta(size=size, freq=old_freq, last_access=self._clock)
        if size > self.capacity:
            # 单条数据大于热层容量: 直接写冷层, 不进内存
            self._write_cold(key, value)
            self._cold.add(key)
            return
        exclude = {key}
        while self._hot_bytes + size > self.capacity:
            victim = self._victim(exclude=exclude)
            if victim is None:
                break
            self._demote(victim)
        if self._hot_bytes + size > self.capacity:
            # 腾不出空间 (防御性处理, 正常不会走到)
            self._write_cold(key, value)
            self._cold.add(key)
            return
        self._hot[key] = value
        self._hot_bytes += size
        self._cold.discard(key)
        p = self._path(key)
        if os.path.exists(p):
            os.unlink(p)

    def _maybe_eager_demote(self) -> None:
        if not self.eager_demote or self.capacity == 0:
            return
        limit = int(self.capacity * self.demote_threshold)
        while self._hot_bytes > limit:
            victim = self._victim()
            if victim is None:
                break
            self._demote(victim)

    def _maybe_prefetch(self) -> None:
        if not self.prefetch or not self._cold:
            return
        candidates = sorted(self._cold, key=self._score, reverse=True)
        promoted = 0
        for key in candidates:
            if promoted >= self.prefetch_count:
                break
            if key not in self._cold:
                continue
            if self._meta[key].size > self.capacity:
                continue
            if self._hot_bytes + self._meta[key].size > self.capacity:
                victim = self._victim()
                if victim is not None and self._score(key) <= self._score(victim):
                    break  # 候选按评分降序, 后面的更不值得换入
            self._promote(key)
            self._meta[key].last_access = self._clock  # 不抬频次, 避免预取污染
            promoted += 1

    # ---------- 公开接口 ----------

    def put(self, key: str, value: bytes | str) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._tick()
        if key in self._hot:
            self._hot_bytes -= self._meta[key].size
            del self._hot[key]
        elif key in self._cold:
            self._cold.discard(key)
            p = self._path(key)
            if os.path.exists(p):
                os.unlink(p)
        self._meta.pop(key, None)
        self._insert_hot(key, value)
        self._touch(key)
        self._maybe_eager_demote()

    def get(self, key: str) -> bytes:
        self._tick()
        start = time.perf_counter_ns()
        try:
            if key in self._hot:
                self.metrics.reads += 1
                self.metrics.hot_hits += 1
                self.metrics.hot_latency_ns += time.perf_counter_ns() - start
                self._touch(key)
                return self._hot[key]
            if key in self._cold:
                self.metrics.reads += 1
                self.metrics.cold_hits += 1
                value = self._promote(key)
                self._touch(key)
                self._maybe_eager_demote()
                self._maybe_prefetch()
                self.metrics.cold_latency_ns += time.perf_counter_ns() - start
                return value
            self.metrics.misses += 1
            raise KeyError(key)
        finally:
            self.metrics.read_latency_ns += time.perf_counter_ns() - start

    def delete(self, key: str) -> None:
        self._tick()
        if key in self._hot:
            self._hot_bytes -= self._meta[key].size
            del self._hot[key]
        elif key in self._cold:
            self._cold.discard(key)
            p = self._path(key)
            if os.path.exists(p):
                os.unlink(p)
        else:
            raise KeyError(key)
        self._meta.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._hot or key in self._cold

    def __len__(self) -> int:
        return len(self._hot) + len(self._cold)

    @property
    def hot_keys(self) -> set[str]:
        return set(self._hot)

    @property
    def cold_keys(self) -> set[str]:
        return set(self._cold)

    @property
    def hot_bytes(self) -> int:
        return self._hot_bytes
