"""A thread-safe skip list (ordered key-value index).

Concurrency model
-----------------
- A single writer (insert/delete) is serialized with a lock.
- Readers (find / items) are lock-free: under CPython's GIL, pointer
  (list-slot) assignment is atomic, and writers splice nodes in an order
  that never exposes a half-linked node:
    * insert: the node's key/value/next pointers are fully initialized
      before the node is linked, level by level; each level's link is a
      single atomic store;
    * delete: the node is marked ``deleted`` first, then unlinked level
      by level. A reader that already holds a reference to the node sees
      the flag and ignores it; the node's own ``next`` pointers are never
      mutated after unlinking, so a reader standing on a deleted node can
      still walk forward to a live successor.
- range_scan returns a SnapshotIterator: the (key, value) pairs in range
  are captured atomically under the writer lock at call time, so the
  iterator observes a consistent snapshot. Inserts/deletes during
  iteration neither skip keys that were live at snapshot time nor cause
  any record to be returned twice.
- rank / select are order-statistics queries over per-level pointer
  widths (an "indexed" skip list). They run under the writer lock so
  the width array is always observed consistently; both cost O(log n),
  the same order as find.

Rank queries
------------
Every pointer carries a ``width``: the number of bottom-level edges it
spans (a pointer to the tail ``None`` spans the edges up to the end of
the list). The rank of a node is the sum of widths along any path from
the head sentinel; ranks are contiguous 1..n in key order. Insert and
delete adjust widths locally at the spliced levels, so the rank
invariant is maintained incrementally. ``select(k)`` returns the k-th
record (1-based) and ``rank(key)`` returns the 1-based rank of a live
key; both agree with positional indexing into an ordered scan.

Reproducibility
---------------
The level generator takes an injected random source (``random.Random``
instance or any object with ``.random()``). Given the same seed and the
same insertion sequence, the produced node-level sequence is identical.
A custom ``level_source`` callable may replace the geometric generator
entirely (used by tests to force all-level-1 or all-max-level layouts).
"""

import random
import threading

__all__ = ["SkipList", "SnapshotIterator"]

_MISSING = object()


class _Node:
    __slots__ = ("key", "value", "next", "width", "deleted")

    def __init__(self, key, value, level):
        self.key = key
        self.value = value
        self.next = [None] * level
        self.width = [0] * level
        self.deleted = False


class SnapshotIterator:
    """Iterator over an immutable point-in-time snapshot of a key range.

    The snapshot is materialized when ``SkipList.range_scan`` is called
    (atomically with respect to writers); consuming the iterator never
    touches the skip list, so concurrent inserts/deletes cannot skip
    snapshot-live keys or produce duplicates. The snapshot holds plain
    (key, value) pairs, not node references, so it does not delay node
    reclamation.
    """

    __slots__ = ("_pairs", "_idx")

    def __init__(self, pairs):
        self._pairs = pairs
        self._idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._idx >= len(self._pairs):
            raise StopIteration
        pair = self._pairs[self._idx]
        self._idx += 1
        return pair

    def __len__(self):
        """Total number of records in the snapshot."""
        return len(self._pairs)

    def remaining(self):
        """Number of records not yet consumed."""
        return len(self._pairs) - self._idx

    def to_list(self):
        """A list of every record in the snapshot (consumes nothing)."""
        return list(self._pairs)


class SkipList:
    def __init__(self, p=0.5, max_level=16, rng=None, level_source=None):
        if not 0.0 < p < 1.0:
            raise ValueError("p must be in (0, 1)")
        if max_level < 1:
            raise ValueError("max_level must be >= 1")
        self._p = p
        self._max_level = max_level
        self._rng = rng if rng is not None else random.Random()
        if level_source is not None:
            self._level_source = level_source
        else:
            self._level_source = self._geometric_level
        # Head is a full-height sentinel; it is never unlinked. Its
        # widths span to the tail: with 0 live nodes the end pseudo-rank
        # is size + 1 = 1 at every level.
        self._head = _Node(None, None, max_level)
        self._head.width = [1] * max_level
        self._level = 1  # highest level currently in use
        self._size = 0
        self._wlock = threading.Lock()

    # ------------------------------------------------------------------
    # level generation
    # ------------------------------------------------------------------
    def _geometric_level(self):
        level = 1
        rand = self._rng.random
        p = self._p
        while level < self._max_level and rand() < p:
            level += 1
        return level

    # ------------------------------------------------------------------
    # read path (lock-free)
    # ------------------------------------------------------------------
    def _find_preds(self, key):
        """Return (predecessor nodes, predecessor ranks) per level.

        ranks[i] is the 1-based rank of preds[i] (the head sentinel has
        rank 0); only levels < self._level are meaningful. Used by
        writers, which must fix pointer widths at the spliced levels.
        """
        preds = [None] * self._max_level
        ranks = [0] * self._max_level
        acc = 0
        node = self._head
        for i in range(self._level - 1, -1, -1):
            nxt = node.next[i]
            while nxt is not None and nxt.key < key:
                acc += node.width[i]
                node = nxt
                nxt = node.next[i]
            preds[i] = node
            ranks[i] = acc
        return preds, ranks

    def find(self, key, default=None):
        """Return the value stored for ``key`` or ``default``."""
        node = self._head
        for i in range(self._level - 1, -1, -1):
            nxt = node.next[i]
            while nxt is not None and nxt.key < key:
                node = nxt
                nxt = node.next[i]
        node = node.next[0]
        if node is not None and node.key == key and not node.deleted:
            return node.value
        return default

    def find_with_steps(self, key):
        """Like ``find`` but also returns the number of pointer hops.

        A "step" is one traversal of a ``next`` pointer (horizontal move)
        plus one per level descended (vertical move). Used by benchmarks
        to compare against the theoretical expectation.
        """
        steps = 0
        node = self._head
        for i in range(self._level - 1, -1, -1):
            nxt = node.next[i]
            while nxt is not None and nxt.key < key:
                node = nxt
                nxt = node.next[i]
                steps += 1
            steps += 1  # level descent
        node = node.next[0]
        found = node is not None and node.key == key and not node.deleted
        return (node.value if found else None), steps

    def __contains__(self, key):
        return self.find(key, _MISSING) is not _MISSING

    def __len__(self):
        return self._size

    # ------------------------------------------------------------------
    # rank queries (consistent view under the writer lock)
    # ------------------------------------------------------------------
    def rank(self, key):
        """1-based rank of ``key`` in key order, or None if absent.

        rank(k) == position of k in sl.items() (1-based). Runs under
        the writer lock so concurrent splices are never observed
        mid-update; O(log n) expected.
        """
        with self._wlock:
            acc = 0
            node = self._head
            for i in range(self._level - 1, -1, -1):
                nxt = node.next[i]
                while nxt is not None and nxt.key < key:
                    acc += node.width[i]
                    node = nxt
                    nxt = node.next[i]
            node = node.next[0]
            if node is not None and node.key == key and not node.deleted:
                return acc + 1
            return None

    def rank_with_steps(self, key):
        """Like ``rank`` but also returns pointer hops (cf. find_with_steps)."""
        with self._wlock:
            steps = 0
            acc = 0
            node = self._head
            for i in range(self._level - 1, -1, -1):
                nxt = node.next[i]
                while nxt is not None and nxt.key < key:
                    acc += node.width[i]
                    node = nxt
                    nxt = node.next[i]
                    steps += 1
                steps += 1  # level descent
            node = node.next[0]
            found = node is not None and node.key == key and not node.deleted
            return (acc + 1 if found else None), steps

    def select(self, k):
        """The k-th record (1-based) in key order, or None if out of range.

        select(k) == sl.items()[k - 1] for 1 <= k <= len(sl). Runs under
        the writer lock; O(log n) expected.
        """
        if k < 1:
            return None
        with self._wlock:
            acc = 0
            node = self._head
            for i in range(self._level - 1, -1, -1):
                while node.next[i] is not None and acc + node.width[i] <= k:
                    acc += node.width[i]
                    node = node.next[i]
            if acc == k and node is not self._head:
                return (node.key, node.value)
            return None

    def select_with_steps(self, k):
        """Like ``select`` but also returns pointer hops."""
        if k < 1:
            return None, 0
        with self._wlock:
            steps = 0
            acc = 0
            node = self._head
            for i in range(self._level - 1, -1, -1):
                while node.next[i] is not None and acc + node.width[i] <= k:
                    acc += node.width[i]
                    node = node.next[i]
                    steps += 1
                steps += 1  # level descent
            if acc == k and node is not self._head:
                return (node.key, node.value), steps
            return None, steps

    def range_scan(self, lo, hi):
        """Snapshot iterator over [(key, value)] for lo <= key <= hi.

        The matching pairs are captured atomically (under the writer
        lock) at call time and returned as a SnapshotIterator. Iteration
        is isolated from later writes: keys live at snapshot time are
        returned exactly once, keys inserted afterwards never appear,
        and keys deleted afterwards still appear. Safe to consume from
        any thread, concurrently with writers.
        """
        with self._wlock:
            pairs = []
            node = self._head.next[0]
            while node is not None and node.key < lo:
                node = node.next[0]
            while node is not None and node.key <= hi:
                if not node.deleted:
                    pairs.append((node.key, node.value))
                node = node.next[0]
        return SnapshotIterator(pairs)

    def items(self):
        """All (key, value) pairs in sorted order."""
        out = []
        node = self._head.next[0]
        while node is not None:
            if not node.deleted:
                out.append((node.key, node.value))
            node = node.next[0]
        return out

    # ------------------------------------------------------------------
    # write path (single writer, serialized)
    # ------------------------------------------------------------------
    def insert(self, key, value):
        """Insert or overwrite. Returns True if a new key was added."""
        with self._wlock:
            preds, ranks = self._find_preds(key)
            nxt = preds[0].next[0]
            if nxt is not None and nxt.key == key and not nxt.deleted:
                nxt.value = value  # atomic store; readers see old or new
                return False
            level = self._level_source()
            if level > self._max_level:
                level = self._max_level
            node = _Node(key, value, level)
            if level > self._level:
                for i in range(self._level, level):
                    preds[i] = self._head
                    ranks[i] = 0
                    # Level i is currently unused: the head pointer spans
                    # the whole list to the tail (end pseudo-rank size+1).
                    self._head.width[i] = self._size + 1
                self._level = level
            r0 = ranks[0]  # new node's rank will be r0 + 1
            for i in range(level):
                node.next[i] = preds[i].next[i]
                node.width[i] = preds[i].width[i] - (r0 - ranks[i])
                preds[i].next[i] = node
                preds[i].width[i] = (r0 - ranks[i]) + 1
            for i in range(level, self._level):
                preds[i].width[i] += 1
            self._size += 1
            return True

    def delete(self, key):
        """Remove ``key``. Returns True if it existed."""
        with self._wlock:
            preds, ranks = self._find_preds(key)
            node = preds[0].next[0]
            if node is None or node.key != key or node.deleted:
                return False
            node.deleted = True  # readers holding it will ignore it
            for i in range(len(node.next)):
                if preds[i].next[i] is node:
                    preds[i].next[i] = node.next[i]
                    preds[i].width[i] += node.width[i] - 1
            for i in range(len(node.next), self._level):
                preds[i].width[i] -= 1
            while self._level > 1 and self._head.next[self._level - 1] is None:
                self._level -= 1
            self._size -= 1
            return True

    # ------------------------------------------------------------------
    # introspection (testing / benchmarking)
    # ------------------------------------------------------------------
    def node_levels(self):
        """[(key, level)] for every live node, in key order."""
        out = []
        node = self._head.next[0]
        while node is not None:
            if not node.deleted:
                out.append((node.key, len(node.next)))
            node = node.next[0]
        return out

    def node_count(self):
        """Number of nodes reachable from level 0."""
        count = 0
        node = self._head.next[0]
        while node is not None:
            count += 1
            node = node.next[0]
        return count

    @property
    def max_level(self):
        return self._max_level

    @property
    def p(self):
        return self._p
