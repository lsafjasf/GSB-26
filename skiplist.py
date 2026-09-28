"""A thread-safe sorted skip list index (Python 3, stdlib only).

Concurrency model
-----------------
- A single writer may insert/delete concurrently with any number of readers.
- Writers are serialized with an internal lock (single-writer assumption).
- Readers are lock-free. Nodes are fully constructed before being published
  with a single forward-pointer store, and unlinking only swings the
  *predecessors'* pointers -- a deleted node's own forward pointers are left
  intact, so a reader currently traversing it keeps walking a valid chain.
  CPython guarantees atomicity of individual reference stores, so readers
  never observe a half-linked node.

Randomized levels
-----------------
The random source is injected: pass ``rand`` (a callable returning floats in
[0, 1), e.g. ``random.Random(seed).random``). Given the same seed and the
same operation sequence, the produced level sequence is fully reproducible.
"""

from __future__ import annotations

import random as _random
import threading
from typing import Any, Callable, Iterator, Optional, Tuple

DEFAULT_MAX_LEVEL = 32
DEFAULT_P = 0.5
_MISSING = object()


class _Node:
    # __weakref__ so tests can observe reclamation of unlinked nodes.
    __slots__ = ("key", "value", "next", "__weakref__")

    def __init__(self, key: Any, value: Any, level: int) -> None:
        self.key = key
        self.value = value
        self.next: list[Optional[_Node]] = [None] * level


def random_level(rand: Callable[[], float], p: float, max_level: int) -> int:
    """Draw a node level from the injected random source."""
    level = 1
    while level < max_level and rand() < p:
        level += 1
    return level


class SkipList:
    """Ordered key -> value index with O(log n) expected operations."""

    def __init__(
        self,
        p: float = DEFAULT_P,
        max_level: int = DEFAULT_MAX_LEVEL,
        rand: Optional[Callable[[], float]] = None,
    ) -> None:
        if not 0.0 < p < 1.0:
            raise ValueError("p must be in (0, 1)")
        if max_level < 1:
            raise ValueError("max_level must be >= 1")
        self.p = p
        self.max_level = max_level
        self._rand = rand if rand is not None else _random.Random().random
        self._head = _Node(None, None, max_level)
        self._level = 1  # current highest level in use
        self._size = 0
        self._lock = threading.Lock()  # serializes writers

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _find_predecessors(self, key: Any, update: list) -> Tuple[Optional[_Node], int]:
        """Locate `key`; fill `update` with predecessors per level.

        Returns (node_with_key_or_None, number_of_pointer_hops).

        A hop is either a forward-pointer traversal or descending to the
        next level and reading that level's forward pointer.
        """
        steps = 0
        node = self._head
        for lvl in range(self._level - 1, -1, -1):
            nxt = node.next[lvl]
            steps += 1
            while nxt is not None and nxt.key < key:
                steps += 1
                node = nxt
                nxt = node.next[lvl]
            update[lvl] = node
        candidate = node.next[0]
        if candidate is not None and candidate.key == key:
            return candidate, steps
        return None, steps

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        return self._size

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def get(self, key: Any, default: Any = None) -> Any:
        """Lock-free point lookup."""
        update: list = [None] * self.max_level
        node, _ = self._find_predecessors(key, update)
        return node.value if node is not None else default

    def search_steps(self, key: Any) -> int:
        """Number of pointer hops a lookup for `key` performs (instrumented)."""
        update: list = [None] * self.max_level
        _, steps = self._find_predecessors(key, update)
        return steps

    def insert(self, key: Any, value: Any) -> bool:
        """Insert or overwrite. Returns True if a new node was created."""
        update: list = [None] * self.max_level
        with self._lock:
            found, _ = self._find_predecessors(key, update)
            if found is not None:
                found.value = value  # in-place value update, no structural change
                return False

            lvl = random_level(self._rand, self.p, self.max_level)
            if lvl > self._level:
                for i in range(self._level, lvl):
                    update[i] = self._head
                self._level = lvl

            new_node = _Node(key, value, lvl)
            # 1) wire the new node's outgoing pointers first ...
            for i in range(lvl):
                new_node.next[i] = update[i].next[i]
            # 2) ... then publish it with atomic predecessor-pointer stores.
            for i in range(lvl):
                update[i].next[i] = new_node
            self._size += 1
            return True

    def delete(self, key: Any) -> bool:
        """Remove `key`. Returns True if it existed."""
        update: list = [None] * self.max_level
        with self._lock:
            found, _ = self._find_predecessors(key, update)
            if found is None:
                return False
            # Swing predecessors past `found`, top-down. `found.next` itself
            # is never mutated, so in-flight readers keep a valid chain and
            # the node becomes garbage once no reader references it.
            for i in range(len(found.next)):
                update[i].next[i] = found.next[i]
            while self._level > 1 and self._head.next[self._level - 1] is None:
                self._level -= 1
            self._size -= 1
            return True

    def scan(self, lo: Any = None, hi: Any = None) -> Iterator[Tuple[Any, Any]]:
        """Yield (key, value) for lo <= key <= hi (None = unbounded).

        Lock-free, forward-only walk of level 0. Keys that stay in the list
        for the whole scan are never skipped; no key is yielded twice.
        """
        node = self._head.next[0]
        if lo is not None:
            while node is not None and node.key < lo:
                node = node.next[0]
        while node is not None:
            if hi is not None and node.key > hi:
                return
            yield node.key, node.value
            node = node.next[0]

    def items(self) -> Iterator[Tuple[Any, Any]]:
        return self.scan()

    # ------------------------------------------------------------------ #
    # introspection (used by tests / benchmarks)
    # ------------------------------------------------------------------ #
    def node_levels(self) -> list[int]:
        """Level of every node, in key order."""
        levels = []
        node = self._head.next[0]
        while node is not None:
            levels.append(len(node.next))
            node = node.next[0]
        return levels

    def total_pointer_slots(self) -> int:
        """Allocated forward-pointer slots (head included) -- memory proxy."""
        return self.max_level + sum(self.node_levels())
