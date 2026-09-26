"""A thread-safe skip list (ordered key-value index).

Concurrency model
-----------------
- A single writer (insert/delete) is serialized with a lock.
- Readers (find / range_scan) are lock-free: under CPython's GIL, pointer
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
- range_scan walks the bottom level strictly forward, therefore it can
  never return the same record twice and never skips a node that stays
  linked ahead of the cursor.

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

__all__ = ["SkipList"]

_MISSING = object()


class _Node:
    __slots__ = ("key", "value", "next", "deleted")

    def __init__(self, key, value, level):
        self.key = key
        self.value = value
        self.next = [None] * level
        self.deleted = False


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
        # Head is a full-height sentinel; it is never unlinked.
        self._head = _Node(None, None, max_level)
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
        """Return predecessor nodes per level; also used by writers."""
        preds = [None] * self._max_level
        node = self._head
        for i in range(self._level - 1, -1, -1):
            nxt = node.next[i]
            while nxt is not None and nxt.key < key:
                node = nxt
                nxt = node.next[i]
            preds[i] = node
        return preds

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

    def range_scan(self, lo, hi):
        """Return [(key, value)] for lo <= key <= hi, sorted, deduplicated.

        Lock-free; safe to run concurrently with inserts/deletes. A key
        deleted mid-scan may or may not appear; a key inserted ahead of
        the cursor may appear. Keys live for the whole scan are always
        returned exactly once.
        """
        out = []
        node = self._head.next[0]
        while node is not None and node.key < lo:
            node = node.next[0]
        while node is not None and node.key <= hi:
            if not node.deleted:
                out.append((node.key, node.value))
            node = node.next[0]
        return out

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
            preds = self._find_preds(key)
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
                self._level = level
            for i in range(level):
                node.next[i] = preds[i].next[i]
                preds[i].next[i] = node
            self._size += 1
            return True

    def delete(self, key):
        """Remove ``key``. Returns True if it existed."""
        with self._wlock:
            preds = self._find_preds(key)
            node = preds[0].next[0]
            if node is None or node.key != key or node.deleted:
                return False
            node.deleted = True  # readers holding it will ignore it
            for i in range(len(node.next)):
                if preds[i].next[i] is node:
                    preds[i].next[i] = node.next[i]
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
