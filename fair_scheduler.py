"""Multi-queue weighted fair scheduler (library, stdlib only).

Algorithm: Start-time Fair Queuing (SFQ), a virtual-time weighted fair
queuing discipline. Each queue keeps a virtual *start* and *finish* tag;
each task has unit cost, so a queue's tags advance by ``1 / weight`` per
served task. The scheduler always serves the active queue with the
smallest finish tag. Empty queues hold no tags and consume no quota:
service is automatically redistributed over the *active* queues in
proportion to their weights.

Fairness bound (proved in README.md): for any two queues i, j that are
continuously non-empty over an interval in which they are served c_i and
c_j times,

    |c_i / w_i - c_j / w_j| <= 1 / w_i + 1 / w_j          (F)

i.e. the normalized lag is bounded by a small constant independent of
the interval length, so the relative deviation from the ideal weight
ratio tends to 0 as the run grows.

Starvation bound (proved in README.md): a task enqueued to a queue i
with weight w_i > 0 is served within

    B_i = 1 + sum_{j != i} ceil(2 * w_j / w_i)            (S)

further dequeues, no matter how backlogged the other queues are.

Time is injectable: pass ``time_fn`` to the constructor. It is used only
for wait-time statistics, never for scheduling decisions, so tests can
drive the scheduler with a fake clock.
"""

from __future__ import annotations

import heapq
import itertools
import math
import time
from collections import deque
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

__all__ = ["FairScheduler"]


class _Queue:
    __slots__ = (
        "name",
        "weight",
        "tasks",
        "start",
        "finish",
        "active",
        "served",
        "total_wait",
    )

    def __init__(self, name: Hashable, weight: float) -> None:
        self.name = name
        self.weight = weight
        self.tasks = deque()  # (task, enqueue_timestamp)
        self.start = 0.0
        self.finish = 0.0
        self.active = False  # True iff present in the scheduling heap
        self.served = 0
        self.total_wait = 0.0


class FairScheduler:
    """Weighted fair multi-queue scheduler.

    - Per-queue FIFO: tasks within one queue are served in arrival order.
    - Across queues: weighted fair (SFQ). A queue with weight w receives
      a share w / sum(active weights) of dequeues while it is backlogged.
    - weight == 0 parks a queue: it is never selected while it has weight
      zero, but tasks may be enqueued and are released by set_weight(>0).
    """

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time_fn = time_fn
        self._queues: Dict[Hashable, _Queue] = {}
        # Heap of (finish, start, seq, name); one live entry per active
        # queue. Stale entries (after weight changes / deactivation) are
        # discarded lazily on pop.
        self._heap: list = []
        self._seq = itertools.count()
        self._v = 0.0  # system virtual time: start tag of last served queue
        self._served_total = 0

    # ------------------------------------------------------------------ #
    # queue management
    # ------------------------------------------------------------------ #

    def add_queue(self, name: Hashable, weight: float = 1.0) -> None:
        """Register a queue. Raises ValueError on duplicate/negative weight."""
        if name in self._queues:
            raise ValueError(f"queue {name!r} already exists")
        if weight < 0:
            raise ValueError("weight must be >= 0")
        self._queues[name] = _Queue(name, float(weight))

    def set_weight(self, name: Hashable, weight: float) -> None:
        """Change a queue's weight; takes effect on the next scheduling round.

        Raising the weight from 0 reactivates a parked queue; setting 0
        parks it (its pending tasks are kept).
        """
        if weight < 0:
            raise ValueError("weight must be >= 0")
        q = self._get(name)
        q.weight = float(weight)
        if q.tasks and weight > 0:
            if not q.active:
                q.active = True
                q.start = max(q.finish, self._v)
            q.finish = q.start + 1.0 / q.weight
            self._push(q)
        elif weight == 0:
            q.active = False  # stale heap entry is ignored on pop

    def remove_queue(self, name: Hashable) -> None:
        """Drop a queue and its pending tasks."""
        q = self._get(name)
        q.active = False
        del self._queues[name]

    # ------------------------------------------------------------------ #
    # core operations
    # ------------------------------------------------------------------ #

    def enqueue(self, name: Hashable, task: Any) -> None:
        """Append a task to a queue (FIFO). O(log Q) when the queue wakes up."""
        q = self._get(name)
        q.tasks.append((task, self._time_fn()))
        if q.weight > 0 and not q.active:
            q.active = True
            q.start = max(q.finish, self._v)
            q.finish = q.start + 1.0 / q.weight
            self._push(q)

    def dequeue(self) -> Optional[Tuple[Hashable, Any]]:
        """Serve one task: returns (queue_name, task), or None if no active
        queue has pending tasks. O(log Q)."""
        while self._heap:
            finish, start, _, name = heapq.heappop(self._heap)
            q = self._queues.get(name)
            if q is None or not q.active or q.finish != finish or q.start != start:
                continue  # stale entry
            task, enqueued_at = q.tasks.popleft()
            now = self._time_fn()
            q.served += 1
            q.total_wait += now - enqueued_at
            self._served_total += 1
            self._v = start
            if q.tasks:
                q.start = q.finish
                q.finish = q.start + 1.0 / q.weight
                self._push(q)
            else:
                q.active = False  # empty queue: releases its quota
            return name, task
        return None

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #

    def pending(self, name: Optional[Hashable] = None) -> int:
        """Number of queued tasks, per queue or in total."""
        if name is not None:
            return len(self._get(name).tasks)
        return sum(len(q.tasks) for q in self._queues.values())

    def starvation_bound(self, name: Hashable) -> Optional[int]:
        """Bound (S): max further dequeues before a task enqueued now to
        queue ``name`` is served, given the currently registered queues.
        Returns None for a weight-0 queue (no finite bound)."""
        q = self._get(name)
        if q.weight == 0:
            return None
        bound = 1
        for other in self._queues.values():
            if other.name != name and other.weight > 0:
                bound += math.ceil(2.0 * other.weight / q.weight)
        return bound

    def stats(self) -> Dict[str, Any]:
        """Snapshot of per-queue and global counters."""
        per_queue = {}
        for name, q in self._queues.items():
            per_queue[name] = {
                "weight": q.weight,
                "pending": len(q.tasks),
                "served": q.served,
                "avg_wait": (q.total_wait / q.served) if q.served else 0.0,
            }
        return {
            "queues": per_queue,
            "served_total": self._served_total,
            "virtual_time": self._v,
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _get(self, name: Hashable) -> _Queue:
        try:
            return self._queues[name]
        except KeyError:
            raise KeyError(f"unknown queue {name!r}") from None

    def _push(self, q: _Queue) -> None:
        heapq.heappush(self._heap, (q.finish, q.start, next(self._seq), q.name))
