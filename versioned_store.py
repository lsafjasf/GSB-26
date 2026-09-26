"""Versioned in-memory key-value store with structural sharing.

Design
------
- Each version is the root of an immutable (persistent) AVL tree.
- Writes use path copying: only the O(log n) nodes on the modified path are
  allocated; every unchanged subtree is shared with all previous versions.
- A version chain is just a list of (root, timestamp). Readers grab a root
  reference and traverse it without any locking; nodes are never mutated, so
  a scan always sees one complete, consistent snapshot of exactly one version.
- Commits are serialized with a lock and are atomic: a batch of mutations
  becomes visible as one new version, never partially.
- gc() drops old roots. Nodes still referenced by retained versions stay
  alive (ordinary refcounting); dropped versions raise VersionNotFoundError.

Standard library only.
"""

from __future__ import annotations

import threading
import time
from bisect import bisect_left

__all__ = ["VersionedStore", "VersionNotFoundError", "DELETE"]


class VersionNotFoundError(LookupError):
    """Raised when reading a version that has been reclaimed or never existed."""

    def __init__(self, version: int):
        self.version = version
        super().__init__(f"version {version} is not available (reclaimed or invalid)")


#: Sentinel used in commit() mutations to delete a key.
DELETE = type("_Delete", (), {"__repr__": lambda self: "DELETE"})()


class _Node:
    __slots__ = ("key", "value", "left", "right", "height", "size")

    def __init__(self, key, value, left, right):
        self.key = key
        self.value = value
        self.left = left
        self.right = right
        self.height = 1 + max(_height(left), _height(right))
        self.size = 1 + _size(left) + _size(right)


def _height(node):
    return node.height if node is not None else 0


def _size(node):
    return node.size if node is not None else 0


def _rotate_left(node):
    right = node.right
    new_left = _Node(node.key, node.value, node.left, right.left)
    return _Node(right.key, right.value, new_left, right.right)


def _rotate_right(node):
    left = node.left
    new_right = _Node(node.key, node.value, left.right, node.right)
    return _Node(left.key, left.value, left.left, new_right)


def _rebalance(node):
    balance = _height(node.left) - _height(node.right)
    if balance > 1:
        left = node.left
        if _height(left.left) < _height(left.right):
            left = _rotate_left(left)
            node = _Node(node.key, node.value, left, node.right)
        return _rotate_right(node)
    if balance < -1:
        right = node.right
        if _height(right.right) < _height(right.left):
            right = _rotate_right(right)
            node = _Node(node.key, node.value, node.left, right)
        return _rotate_left(node)
    return node


def _insert(node, key, value):
    if node is None:
        return _Node(key, value, None, None)
    if key == node.key:
        return _Node(key, value, node.left, node.right)
    if key < node.key:
        new = _Node(node.key, node.value, _insert(node.left, key, value), node.right)
    else:
        new = _Node(node.key, node.value, node.left, _insert(node.right, key, value))
    return _rebalance(new)


def _delete(node, key):
    """Returns (new_node, deleted_flag)."""
    if node is None:
        return None, False
    if key < node.key:
        new_left, deleted = _delete(node.left, key)
        if not deleted:
            return node, False
        return _rebalance(_Node(node.key, node.value, new_left, node.right)), True
    if key > node.key:
        new_right, deleted = _delete(node.right, key)
        if not deleted:
            return node, False
        return _rebalance(_Node(node.key, node.value, node.left, new_right)), True
    # Found the node to delete.
    if node.left is None:
        return node.right, True
    if node.right is None:
        return node.left, True
    # Replace with in-order successor (min of right subtree).
    successor = node.right
    while successor.left is not None:
        successor = successor.left
    new_right, _ = _delete(node.right, successor.key)
    return _rebalance(_Node(successor.key, successor.value, node.left, new_right)), True


def _find(node, key, default):
    while node is not None:
        if key == node.key:
            return node.value
        node = node.left if key < node.key else node.right
    return default


def _scan(node, lo, hi, out):
    """In-order traversal collecting (key, value) with lo <= key <= hi.

    lo/hi of None mean unbounded. Iterative, prunes out-of-range subtrees.
    """
    stack = []
    current = node
    while stack or current is not None:
        while current is not None:
            if lo is None or current.key >= lo:
                stack.append(current)
                current = current.left
            else:
                current = current.right
        if not stack:
            break
        entry = stack.pop()
        if hi is not None and entry.key > hi:
            break
        out.append((entry.key, entry.value))
        current = entry.right


class VersionedStore:
    """A versioned in-memory sorted key-value store.

    - commit(mutations) applies a batch atomically and returns the new version.
    - get/scan take an optional version (default: latest).
    - gc(min_version=... / before_time=...) reclaims old versions.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Version 0 is the empty store. _roots[i] holds version _base + i.
        self._roots = [None]
        self._times = [time.time()]
        self._base = 0

    # ------------------------------------------------------------- versions

    @property
    def latest_version(self) -> int:
        return self._base + len(self._roots) - 1

    def versions(self):
        """List of currently live (readable) version numbers."""
        return list(range(self._base, self.latest_version + 1))

    def version_time(self, version: int) -> float:
        return self._times[self._index(version)]

    def _index(self, version: int) -> int:
        index = version - self._base
        if not 0 <= index < len(self._roots):
            raise VersionNotFoundError(version)
        return index

    def _root_at(self, version):
        if version is None:
            return self._roots[-1]
        return self._roots[self._index(version)]

    # -------------------------------------------------------------- writing

    def commit(self, mutations) -> int:
        """Atomically apply [(key, value), ...]; value=DELETE deletes the key.

        Returns the new version number. The whole batch becomes visible as a
        single version; readers never observe a partial batch.
        """
        mutations = list(mutations)
        with self._lock:
            root = self._roots[-1]
            for key, value in mutations:
                if value is DELETE:
                    root, _ = _delete(root, key)
                else:
                    root = _insert(root, key, value)
            self._roots.append(root)
            self._times.append(time.time())
            return self.latest_version

    def put(self, key, value) -> int:
        return self.commit([(key, value)])

    def delete(self, key) -> int:
        return self.commit([(key, DELETE)])

    # -------------------------------------------------------------- reading

    def get(self, key, version=None, default=None):
        """Value of key at version (default: latest), or default if absent."""
        return _find(self._root_at(version), key, default)

    def scan(self, lo=None, hi=None, version=None):
        """All (key, value) pairs with lo <= key <= hi at one version.

        The result is always a complete view of exactly one version: the root
        is captured once and the tree it points to is immutable.
        """
        out = []
        _scan(self._root_at(version), lo, hi, out)
        return out

    def count(self, version=None) -> int:
        return _size(self._root_at(version))

    # ------------------------------------------------------------------ gc

    def gc(self, min_version: int | None = None, before_time: float | None = None):
        """Reclaim versions older than the given version and/or timestamp.

        Versions < min_version and versions committed before `before_time`
        are dropped. The latest version is always retained. Returns the list
        of dropped version numbers. Dropped versions raise
        VersionNotFoundError on read; retained versions remain fully correct.
        """
        with self._lock:
            latest = self.latest_version
            cutoff = self._base
            if min_version is not None:
                cutoff = max(cutoff, min_version)
            if before_time is not None:
                cutoff = max(cutoff, self._base + bisect_left(self._times, before_time))
            cutoff = min(cutoff, latest)  # never drop the latest version
            dropped = list(range(self._base, cutoff))
            del self._roots[: cutoff - self._base]
            del self._times[: cutoff - self._base]
            self._base = cutoff
            return dropped
