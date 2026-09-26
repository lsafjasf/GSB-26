"""Versioned in-memory key-value store with a version chain.

Design: a persistent AVL tree implemented with path copying. Every commit
creates only O(log n) new nodes along the modified path(s); all untouched
subtrees are shared structurally with every previous version. A version is
just an immutable root pointer, so a reader that has resolved a version sees
a complete, consistent snapshot of that version no matter what writers do
concurrently.

Reads never take the lock while walking the tree (nodes are immutable once
created); only version-id -> root resolution and commits are serialized.

Standard library only. Python 3.8+.
"""

from __future__ import annotations

import threading
import time

__all__ = ["VersionedStore", "VersionNotFoundError"]


class VersionNotFoundError(KeyError):
    """Raised when reading a version that was reclaimed or never existed."""


def _height(node):
    return node.height if node is not None else 0


class _Node:
    __slots__ = ("key", "value", "left", "right", "height")

    def __init__(self, key, value, left, right):
        self.key = key
        self.value = value
        self.left = left
        self.right = right
        self.height = 1 + max(_height(left), _height(right))


def _mk(key, value, left, right, ctr):
    ctr[0] += 1
    return _Node(key, value, left, right)


def _rot_left(node, ctr):
    r = node.right
    new_node = _mk(node.key, node.value, node.left, r.left, ctr)
    return _mk(r.key, r.value, new_node, r.right, ctr)


def _rot_right(node, ctr):
    l = node.left
    new_node = _mk(node.key, node.value, l.right, node.right, ctr)
    return _mk(l.key, l.value, l.left, new_node, ctr)


def _rebalance(node, ctr):
    if _height(node.left) - _height(node.right) > 1:
        left = node.left
        if _height(left.right) > _height(left.left):
            left = _rot_left(left, ctr)
        return _rot_right(_mk(node.key, node.value, left, node.right, ctr), ctr)
    if _height(node.right) - _height(node.left) > 1:
        right = node.right
        if _height(right.left) > _height(right.right):
            right = _rot_right(right, ctr)
        return _rot_left(_mk(node.key, node.value, node.left, right, ctr), ctr)
    return node


def _insert(node, key, value, ctr):
    if node is None:
        return _mk(key, value, None, None, ctr)
    if key < node.key:
        return _rebalance(
            _mk(node.key, node.value, _insert(node.left, key, value, ctr), node.right, ctr),
            ctr,
        )
    if key > node.key:
        return _rebalance(
            _mk(node.key, node.value, node.left, _insert(node.right, key, value, ctr), ctr),
            ctr,
        )
    # overwrite existing key: still path-copy so old versions are untouched
    return _mk(key, value, node.left, node.right, ctr)


def _delete(node, key, ctr):
    """Return (new_subtree_root, found)."""
    if node is None:
        return None, False
    if key < node.key:
        new_left, found = _delete(node.left, key, ctr)
        if not found:
            return node, False
        return _rebalance(_mk(node.key, node.value, new_left, node.right, ctr), ctr), True
    if key > node.key:
        new_right, found = _delete(node.right, key, ctr)
        if not found:
            return node, False
        return _rebalance(_mk(node.key, node.value, node.left, new_right, ctr), ctr), True
    if node.left is None:
        return node.right, True
    if node.right is None:
        return node.left, True
    succ = node.right
    while succ.left is not None:
        succ = succ.left
    new_right, _ = _delete(node.right, succ.key, ctr)
    return _rebalance(_mk(succ.key, succ.value, node.left, new_right, ctr), ctr), True


def _range_iter(root, start, end, limit):
    """In-order iterator over [start, end). start/end=None means unbounded."""
    stack = []
    cur = root
    emitted = 0
    while cur is not None or stack:
        while cur is not None:
            if start is not None and cur.key < start:
                cur = cur.right  # cur and its whole left subtree are < start
            else:
                stack.append(cur)
                cur = cur.left
        if not stack:
            return
        node = stack.pop()
        if end is not None and node.key >= end:
            return
        yield node.key, node.value
        emitted += 1
        if limit is not None and emitted >= limit:
            return
        cur = node.right


class _Version:
    __slots__ = ("version_id", "timestamp", "root", "nodes_created", "updates")

    def __init__(self, version_id, timestamp, root, nodes_created, updates):
        self.version_id = version_id
        self.timestamp = timestamp
        self.root = root
        self.nodes_created = nodes_created
        self.updates = updates


class VersionedStore:
    """A version-chained in-memory KV store.

    Keys must be mutually orderable (e.g. all int or all str). Values are
    arbitrary Python objects.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._versions = {}          # version_id -> _Version
        self._latest_id = None
        self._next_id = 1
        self._reclaimed_high_water = 0
        self.total_nodes_created = 0  # cumulative, for space accounting
        self.total_updates = 0

    # ------------------------------------------------------------------ writes

    def commit(self, updates=None, deletes=None, timestamp=None):
        """Apply a batch of updates/deletes atomically as one new version.

        `updates` may be a dict or an iterable of (key, value) pairs.
        `deletes` is an iterable of keys; missing keys are ignored.
        Returns the new version id.
        """
        if updates is None and deletes is None:
            updates = {}
        with self._lock:
            ctr = [0]
            root = self._versions[self._latest_id].root if self._latest_id else None
            n_updates = 0
            if updates:
                items = updates.items() if hasattr(updates, "items") else updates
                for key, value in items:
                    root = _insert(root, key, value, ctr)
                    n_updates += 1
            if deletes:
                for key in deletes:
                    root, found = _delete(root, key, ctr)
                    if found:
                        n_updates += 1
            vid = self._next_id
            self._next_id += 1
            ver = _Version(
                vid,
                time.time() if timestamp is None else timestamp,
                root,
                ctr[0],
                n_updates,
            )
            self._versions[vid] = ver
            self._latest_id = vid
            self.total_nodes_created += ctr[0]
            self.total_updates += n_updates
            return vid

    def put(self, key, value, timestamp=None):
        """Convenience: commit a single-key update as a new version."""
        return self.commit(updates={key: value}, timestamp=timestamp)

    def delete(self, key, timestamp=None):
        """Convenience: commit a single-key delete. Raises KeyError if absent."""
        with self._lock:
            root = self._versions[self._latest_id].root if self._latest_id else None
        node = root
        while node is not None:
            if key < node.key:
                node = node.left
            elif key > node.key:
                node = node.right
            else:
                break
        else:
            pass
        if node is None:
            raise KeyError(key)
        return self.commit(deletes=[key], timestamp=timestamp)

    # ------------------------------------------------------------------- reads

    def _resolve(self, version):
        vid = self._latest_id if version is None else version
        if vid is None:
            return None  # empty store
        ver = self._versions.get(vid)
        if ver is None:
            if isinstance(vid, int) and vid <= self._reclaimed_high_water:
                raise VersionNotFoundError(
                    "version %r has been reclaimed by gc" % (vid,)
                )
            raise VersionNotFoundError("version %r does not exist" % (vid,))
        return ver

    def get(self, key, version=None):
        """Point read at a version (default: latest). Raises KeyError if the
        key is absent, VersionNotFoundError if the version is gone."""
        with self._lock:
            ver = self._resolve(version)
        node = ver.root if ver is not None else None
        while node is not None:
            if key < node.key:
                node = node.left
            elif key > node.key:
                node = node.right
            else:
                return node.value
        raise KeyError(key)

    def scan(self, version=None, start=None, end=None, limit=None):
        """Range scan over [start, end) at a version (default: latest).

        The version is resolved eagerly under the lock; the returned iterator
        then walks immutable nodes lock-free, so it always yields one complete
        snapshot of exactly one version, never a mix.
        """
        with self._lock:
            ver = self._resolve(version)
        root = ver.root if ver is not None else None
        return _range_iter(root, start, end, limit)

    def __contains__(self, key):
        try:
            self.get(key)
            return True
        except KeyError:
            return False

    # --------------------------------------------------------------------- gc

    def gc(self, min_version):
        """Reclaim every version strictly older than `min_version`.

        `min_version` itself and all newer versions stay fully readable.
        Returns the number of reclaimed versions.
        """
        with self._lock:
            if min_version not in self._versions:
                raise VersionNotFoundError(
                    "gc watermark version %r is not retained" % (min_version,)
                )
            doomed = [vid for vid in self._versions if vid < min_version]
            for vid in doomed:
                del self._versions[vid]
            if doomed:
                self._reclaimed_high_water = max(
                    self._reclaimed_high_water, max(doomed)
                )
            return len(doomed)

    def gc_before_time(self, timestamp):
        """Reclaim by wall-clock time. Keeps the newest version with
        timestamp <= `timestamp` (the time-travel watermark) and everything
        newer; reclaims all versions with a smaller version id.

        Assumes commit timestamps are non-decreasing (true for real time).
        Returns the number of reclaimed versions.
        """
        with self._lock:
            candidates = [
                v for v in self._versions.values() if v.timestamp <= timestamp
            ]
            if not candidates:
                return 0
            watermark = max(candidates, key=lambda v: (v.timestamp, v.version_id))
        return self.gc(watermark.version_id)

    # ------------------------------------------------------------------ stats

    @property
    def latest_version(self):
        return self._latest_id

    def version_ids(self):
        with self._lock:
            return sorted(self._versions)

    def version_count(self):
        with self._lock:
            return len(self._versions)

    def info(self, version=None):
        with self._lock:
            ver = self._resolve(version)
        if ver is None:
            return None
        return {
            "version": ver.version_id,
            "timestamp": ver.timestamp,
            "nodes_created": ver.nodes_created,
            "updates": ver.updates,
        }

    def live_node_count(self):
        """Distinct tree nodes reachable from all retained versions.

        This is the actual memory footprint of the store's index; shared
        subtrees are counted once.
        """
        with self._lock:
            roots = [v.root for v in self._versions.values()]
        seen = set()
        for root in roots:
            stack = [root]
            while stack:
                node = stack.pop()
                if node is None or id(node) in seen:
                    continue
                seen.add(id(node))
                stack.append(node.left)
                stack.append(node.right)
        return len(seen)
