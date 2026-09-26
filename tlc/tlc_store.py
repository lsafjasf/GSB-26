"""Thread-local cache + global merge counter store (stdlib only).

Design
------
- Each thread writes to its own ``_LocalCache`` (no shared state on the
  write hot path, so no inter-thread contention for reads/writes).
- A merge moves a *batch* of deltas from a local cache into the global
  map. Every batch is tagged with ``(source_id, seq)`` where ``seq`` is a
  per-source monotonically increasing sequence number. The global side
  keeps a high-water mark per source; a batch whose ``seq`` is not
  greater than the high-water mark is dropped. This makes merges
  *idempotent*: re-merging the same batch is a no-op.
- Global reads take the global lock and return a point-in-time snapshot:
  every batch committed before the read is fully included, every batch
  committed after is fully excluded. There is no partial batch visibility
  because a batch is applied atomically under the global lock.
- Thread exit policy: **merge-on-exit (no data loss)**. Each local cache
  is registered in the store together with a weakref to its owner thread.
  ``_reap_dead()`` (invoked on every global read/merge and also callable
  directly) merges caches whose owner thread has died. We choose
  merge-on-exit over discard because the store's contract is a monotone
  counter: silently dropping committed local writes would lose counts
  and surprise callers. Discard is still available explicitly via
  ``discard_local()`` for callers that opt in.
- Invariants (checked by ``assert_invariants()``):
    1. global total == sum of per-source merged totals
    2. global total is monotonically non-decreasing over time
       (no delete API exists; ``discard_local`` only drops *unmerged*
       local deltas, never merged global state)
"""

from __future__ import annotations

import threading
import weakref
from typing import Dict, Iterator, Tuple


class _LocalCache:
    """Per-thread delta buffer. All methods are thread-confined except
    ``snapshot_batch``/``commit_batch``/``rollback_batch`` which are
    called by the store (possibly from another thread during reaping),
    so they are guarded by an internal lock."""

    __slots__ = ("source_id", "_lock", "_data", "_seq", "_inflight")

    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        self._lock = threading.Lock()
        self._data: Dict[str, int] = {}
        self._seq = 0
        # Batches swapped out but not yet confirmed by the global side.
        self._inflight: Dict[int, Dict[str, int]] = {}

    # ---- thread-confined hot path (owner thread only) ----
    def add(self, key: str, delta: int = 1) -> None:
        # Fast path: no locking needed for correctness of the owner
        # thread's own writes; the lock only matters when a snapshot
        # races with a write. We take it anyway to keep snapshot atomic
        # w.r.t. concurrent writes (reaper thread may snapshot us).
        with self._lock:
            self._data[key] = self._data.get(key, 0) + delta

    def pending(self) -> int:
        with self._lock:
            return sum(self._data.values())

    # ---- store-facing batch protocol ----
    def snapshot_batch(self) -> Tuple[int, int, Dict[str, int]]:
        """Swap out current deltas as a new batch. Returns
        (source_id, seq, deltas). Empty caches yield an empty batch."""
        with self._lock:
            self._seq += 1
            batch = self._data
            self._data = {}
            if batch:
                self._inflight[self._seq] = batch
            return self.source_id, self._seq, batch

    def commit_batch(self, seq: int) -> None:
        with self._lock:
            self._inflight.pop(seq, None)

    def rollback_batch(self, seq: int) -> None:
        """Put an uncommitted batch back (e.g. merge failed midway)."""
        with self._lock:
            batch = self._inflight.pop(seq, None)
            if batch:
                for k, v in batch.items():
                    self._data[k] = self._data.get(k, 0) + v

    def discard(self) -> None:
        with self._lock:
            self._data.clear()
            self._inflight.clear()


class ThreadLocalMergeStore:
    """Counter store: thread-local writes, consistent global reads."""

    def __init__(self) -> None:
        self._glock = threading.Lock()
        self._global: Dict[str, int] = {}
        # source_id -> highest applied batch seq (high-water mark)
        self._applied_seq: Dict[int, int] = {}
        # source_id -> total merged contribution (for invariant checks)
        self._merged_per_source: Dict[int, int] = {}
        self._total = 0
        self._last_observed_total = 0

        self._reg_lock = threading.Lock()
        self._sources = 0
        # source_id -> (cache, weakref to owner thread)
        self._registry: Dict[int, Tuple[_LocalCache, "weakref.ref[threading.Thread]"]] = {}
        self._tls = threading.local()

    # ---------------- local (thread-confined) API ----------------

    def _cache(self) -> _LocalCache:
        cache = getattr(self._tls, "cache", None)
        if cache is None:
            with self._reg_lock:
                self._sources += 1
                sid = self._sources
            cache = _LocalCache(sid)
            self._registry[sid] = (cache, weakref.ref(threading.current_thread()))
            self._tls.cache = cache
        return cache

    def add(self, key: str, delta: int = 1) -> None:
        """Record a local increment. Never touches shared state."""
        self._cache().add(key, delta)

    def pending(self) -> int:
        """Unmerged local delta total for the calling thread."""
        return self._cache().pending()

    # ---------------- merge ----------------

    def _apply_batch(self, source_id: int, seq: int, deltas: Dict[str, int]) -> bool:
        """Apply one batch to the global map, idempotently.

        Returns True if the batch was applied, False if it was a
        duplicate (seq <= high-water mark) and therefore ignored.
        """
        if not deltas:
            return True
        with self._glock:
            if seq <= self._applied_seq.get(source_id, 0):
                return False  # duplicate merge: no-op (idempotency)
            for k, v in deltas.items():
                self._global[k] = self._global.get(k, 0) + v
                self._total += v
            self._applied_seq[source_id] = seq
            self._merged_per_source[source_id] = (
                self._merged_per_source.get(source_id, 0) + sum(deltas.values())
            )
            return True

    def merge_batch(self, source_id: int, seq: int, deltas: Dict[str, int]) -> bool:
        """Public low-level merge of an explicit batch (used by tests to
        prove idempotency: calling twice with the same (source_id, seq)
        must count the data exactly once)."""
        return self._apply_batch(source_id, seq, deltas)

    def flush(self) -> int:
        """Merge the calling thread's local cache into the global map.
        Returns the number of newly applied counts (0 if nothing new)."""
        cache = self._cache()
        sid, seq, batch = cache.snapshot_batch()
        try:
            applied = self._apply_batch(sid, seq, batch)
        except BaseException:
            cache.rollback_batch(seq)
            raise
        cache.commit_batch(seq)
        return sum(batch.values()) if applied else 0

    def flush_all(self) -> None:
        """Merge every registered local cache (live and dead threads)."""
        self._reap_dead()
        with self._reg_lock:
            caches = [c for c, _ in self._registry.values()]
        for cache in caches:
            sid, seq, batch = cache.snapshot_batch()
            try:
                self._apply_batch(sid, seq, batch)
            except BaseException:
                cache.rollback_batch(seq)
                raise
            cache.commit_batch(seq)

    # ---------------- thread-exit handling ----------------

    def close(self) -> None:
        """Merge-on-exit: flush this thread's cache and deregister it.
        Safe to call multiple times."""
        cache = getattr(self._tls, "cache", None)
        if cache is None:
            return
        self.flush()
        with self._reg_lock:
            self._registry.pop(cache.source_id, None)
        self._tls.cache = None

    def discard_local(self) -> None:
        """Explicitly drop this thread's unmerged local deltas (opt-in
        discard policy). Never affects already-merged global state."""
        cache = getattr(self._tls, "cache", None)
        if cache is not None:
            cache.discard()

    def _reap_dead(self) -> None:
        """Merge caches whose owner thread has exited (no data loss)."""
        with self._reg_lock:
            items = list(self._registry.items())
        for sid, (cache, tref) in items:
            t = tref()
            if t is not None and not t.is_alive():
                sid_, seq, batch = cache.snapshot_batch()
                try:
                    self._apply_batch(sid_, seq, batch)
                except BaseException:
                    cache.rollback_batch(seq)
                    raise
                cache.commit_batch(seq)

    # ---------------- global consistent reads ----------------

    def snapshot(self) -> Dict[str, int]:
        """Point-in-time consistent copy of the merged global view."""
        self._reap_dead()
        with self._glock:
            return dict(self._global)

    def snapshot_with_total(self) -> Tuple[Dict[str, int], int]:
        """Atomic point-in-time (view, total). Prefer this over calling
        snapshot() and total() separately: two separate reads can straddle
        a merge."""
        self._reap_dead()
        with self._glock:
            return dict(self._global), self._total

    def get(self, key: str, default: int = 0) -> int:
        self._reap_dead()
        with self._glock:
            return self._global.get(key, default)

    def total(self) -> int:
        self._reap_dead()
        with self._glock:
            return self._total

    # ---------------- invariants ----------------

    def assert_invariants(self) -> None:
        with self._glock:
            per_source_sum = sum(self._merged_per_source.values())
            assert self._total == per_source_sum, (
                f"total({self._total}) != sum of merged per-source "
                f"contributions({per_source_sum})"
            )
            assert self._total == sum(self._global.values()), (
                "total does not match sum of global map values"
            )
            assert self._total >= self._last_observed_total, (
                f"total regressed: {self._last_observed_total} -> {self._total}"
            )
            self._last_observed_total = self._total

    def registered_sources(self) -> int:
        with self._reg_lock:
            return len(self._registry)
