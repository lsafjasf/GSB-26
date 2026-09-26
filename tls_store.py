"""Thread-local cached, globally merged counter store (stdlib only).

Design
------
- Each writer thread owns a `_Source`: a private dict of *cumulative* counts
  (key -> monotonically non-decreasing total). Writes never touch shared
  state, so the hot path only takes an uncontended per-source lock.
- The global store keeps, per source, the last *merged snapshot* of that
  source's cumulative dict. Merging is **assignment, not addition**
  (CRDT grow-only style): merge(source) replaces the stored snapshot with
  the source's current cumulative values. Re-merging the same state is
  therefore a no-op -> idempotent by construction.
- A consistent global view is obtained under one global lock by summing
  every source's merged contribution (snapshot minus per-source base).
  Because each source's cumulative counts are monotonic and merges only
  replace snapshots with >= values, the global total is monotonically
  non-decreasing between explicit `reset()` calls.
- Thread exit policy: **merge-on-exit, never discard**. A daemon reaper
  thread periodically merges every live source and, when it finds a dead
  thread's source, performs a final merge before deregistering it.
  Rationale: the store's contract is "no committed write is ever lost or
  double counted"; discarding would silently lose counts, while
  merge-on-exit is safe because merge is idempotent (a late/extra final
  merge cannot corrupt anything). `flush_all()` offers a deterministic
  synchronous equivalent for tests and shutdown.

Lock ordering (deadlock-free): global lock -> source lock. Writers only
ever take their own source lock.
"""

from __future__ import annotations

import threading
import weakref


class _Source:
    __slots__ = ("source_id", "thread_ref", "thread_name", "lock",
                 "cumulative", "base")

    def __init__(self, source_id: int, thread: threading.Thread):
        self.source_id = source_id
        self.thread_ref = weakref.ref(thread)
        self.thread_name = thread.name
        self.lock = threading.Lock()
        self.cumulative: dict[str, int] = {}
        self.base: dict[str, int] = {}


class CounterStore:
    """Thread-local write cache + idempotent global merge."""

    def __init__(self, reap_interval: float = 0.05, start_reaper: bool = True):
        self._lock = threading.Lock()          # guards _sources / _merged
        self._local = threading.local()
        self._sources: dict[int, _Source] = {}
        # source_id -> (cumulative snapshot, base snapshot) at merge time
        self._merged: dict[int, tuple[dict[str, int], dict[str, int]]] = {}
        self._next_id = 0
        self._reap_interval = reap_interval
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if start_reaper:
            self._reaper = threading.Thread(
                target=self._reap_loop,
                name="counter-store-reaper",
                daemon=True,
            )
            self._reaper.start()

    # ------------------------------------------------------------------
    # Thread-local write path
    # ------------------------------------------------------------------
    def _current_source(self) -> _Source:
        src = getattr(self._local, "src", None)
        if src is None:
            thread = threading.current_thread()
            with self._lock:
                src = _Source(self._next_id, thread)
                self._next_id += 1
                self._sources[src.source_id] = src
            self._local.src = src
        return src

    def add(self, key: str, n: int = 1) -> None:
        """Thread-local increment. Hot path: uncontended per-source lock."""
        if n < 0:
            raise ValueError("n must be >= 0 (counts are monotonic); "
                             "use reset() for explicit deletion")
        src = self._current_source()
        with src.lock:
            src.cumulative[key] = src.cumulative.get(key, 0) + n

    # ------------------------------------------------------------------
    # Merge (idempotent)
    # ------------------------------------------------------------------
    def merge(self) -> None:
        """Merge the calling thread's local cache into the global view."""
        self._merge_source(self._current_source())

    def _merge_source(self, src: _Source) -> None:
        # Assignment semantics: replace the stored snapshot. Merging the
        # same cumulative state twice yields the identical stored snapshot,
        # so merges are idempotent and safe to retry.
        with self._lock:
            with src.lock:
                self._merged[src.source_id] = (dict(src.cumulative),
                                               dict(src.base))

    def flush_all(self) -> None:
        """Synchronously merge every source; finalize dead threads' sources."""
        for src in list(self._sources.values()):
            self._merge_source(src)
            thread = src.thread_ref()
            if thread is None or not thread.is_alive():
                self._deregister(src)

    def close_local(self) -> None:
        """Final merge + deregister for the calling thread (explicit exit)."""
        src = getattr(self._local, "src", None)
        if src is None:
            return
        self._merge_source(src)
        self._deregister(src)
        self._local.src = None

    def _deregister(self, src: _Source) -> None:
        with self._lock:
            self._sources.pop(src.source_id, None)
        # NB: _merged keeps the source's last snapshot, so its counts
        # remain part of the global view forever (until reset()).

    # ------------------------------------------------------------------
    # Global consistent reads
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, int]:
        """Consistent global view: every merged source is either fully
        included or fully excluded; no partial or double counting."""
        with self._lock:
            entries = list(self._merged.values())
        view: dict[str, int] = {}
        for cumulative, base in entries:
            for key, value in cumulative.items():
                contribution = value - base.get(key, 0)
                if contribution:
                    view[key] = view.get(key, 0) + contribution
        return view

    def total(self) -> int:
        return sum(self.snapshot().values())

    def source_totals(self) -> dict[int, int]:
        """Per-source merged totals; used to assert the invariant
        total == sum(per-source merged totals)."""
        with self._lock:
            entries = dict(self._merged)
        result = {}
        for source_id, (cumulative, base) in entries.items():
            result[source_id] = sum(
                value - base.get(key, 0)
                for key, value in cumulative.items()
            )
        return result

    def check_invariants(self) -> None:
        """Raise AssertionError if the global-view invariant is violated."""
        totals = self.source_totals()
        assert all(value >= 0 for value in totals.values()), \
            f"negative contribution: {totals}"
        assert sum(totals.values()) == self.total(), "total mismatch"

    # ------------------------------------------------------------------
    # Explicit deletion
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Explicit deletion: zero the global view. Monotonicity may be
        violated across this call (by design); never within two resets."""
        with self._lock:
            sources = list(self._sources.values())
            for src in sources:
                with src.lock:
                    src.base = dict(src.cumulative)
            self._merged.clear()

    # ------------------------------------------------------------------
    # Reaper: merge-on-exit for threads that never call close_local()
    # ------------------------------------------------------------------
    def _reap_loop(self) -> None:
        while not self._stop.wait(self._reap_interval):
            self.flush_all()

    def close(self) -> None:
        """Stop the reaper and do a final flush. Store remains readable."""
        self._stop.set()
        if self._reaper is not None:
            self._reaper.join(timeout=2.0)
        self.flush_all()

    def __enter__(self) -> "CounterStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
