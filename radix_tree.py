"""Compressed prefix tree (radix tree) ordered key-value store.

Keys are ``bytes`` (or anything accepted by ``bytes()``), so binary keys
containing NUL bytes and arbitrarily long keys are supported.  Shared
prefixes are stored once on edges, so memory is proportional to the
number of *branching* positions rather than the total key length.

Representation
--------------
To actually save memory under CPython (where every object carries ~50
bytes of overhead), the tree is stored in flat typed arrays instead of
per-node objects:

- ``_labels``:  one ``bytearray`` arena holding every edge label.
- edge arrays:  ``_e_first`` / ``_e_off`` / ``_e_len`` / ``_e_child``
  (first byte, arena offset, label length, child node index).
- node arrays:  ``_n_start`` / ``_n_count`` (each node's edges occupy a
  contiguous segment of the edge arrays) and ``_values`` (per-node value
  or the ``_MISSING`` sentinel).

Arrays are append-only: inserting an edge into a node copies its (tiny)
segment to the end of the arrays.  Edge splits reuse the existing arena
bytes (the head keeps its offset, the tail is already there), so inserts
create no label garbage; only delete-triggered merges may leave a small
amount of unreachable arena bytes.

All operations are iterative, so pathological key lengths cannot
overflow the Python call stack.
"""

from __future__ import annotations

import sys
from array import array
from typing import Iterator, Optional, Tuple

_MISSING = object()


class RadixTree:
    """Ordered map from ``bytes`` keys to arbitrary values.

    Supports insertion, exact lookup, deletion, lexicographic iteration
    and prefix/range queries.  Iteration order is byte-wise
    lexicographic.
    """

    __slots__ = (
        "_labels", "_e_first", "_e_off", "_e_len", "_e_child",
        "_n_start", "_n_count", "_values", "_size",
    )

    def __init__(self) -> None:
        self._labels = bytearray()
        self._e_first = array("B")   # first byte of the edge label
        self._e_off = array("q")     # offset of the label in the arena
        self._e_len = array("q")     # label length
        self._e_child = array("q")   # child node index
        self._n_start = array("q")   # start of the node's edge segment
        self._n_count = array("q")   # number of edges of the node
        self._values = []            # per node: value or _MISSING
        self._size = 0
        self._new_node()             # node 0 is the root

    # ------------------------------------------------------------ node/edge mgmt
    def _new_node(self) -> int:
        self._n_start.append(len(self._e_first))
        self._n_count.append(0)
        self._values.append(_MISSING)
        return len(self._values) - 1

    def _find_edge(self, node: int, first_byte: int) -> int:
        """Return the edge slot of ``node`` starting with ``first_byte``."""
        start = self._n_start[node]
        stop = start + self._n_count[node]
        first = self._e_first
        for slot in range(start, stop):
            if first[slot] == first_byte:
                return slot
        return -1

    def _add_edge(self, node: int, first_byte: int, off: int, length: int,
                  child: int) -> None:
        """Append an edge to ``node`` (segments are append-only copies)."""
        start = self._n_start[node]
        count = self._n_count[node]
        end = len(self._e_first)
        if start + count != end:
            # relocate the existing segment to the end of the arrays
            self._e_first.extend(self._e_first[start:start + count])
            self._e_off.extend(self._e_off[start:start + count])
            self._e_len.extend(self._e_len[start:start + count])
            self._e_child.extend(self._e_child[start:start + count])
        self._e_first.append(first_byte)
        self._e_off.append(off)
        self._e_len.append(length)
        self._e_child.append(child)
        self._n_start[node] = len(self._e_first) - (count + 1)
        self._n_count[node] = count + 1

    def _remove_edge(self, node: int, slot: int) -> None:
        """Remove edge ``slot`` from ``node`` (append-only copy)."""
        start = self._n_start[node]
        count = self._n_count[node]
        keep = [k for k in range(start, start + count) if k != slot]
        self._e_first.extend(self._e_first[k] for k in keep)
        self._e_off.extend(self._e_off[k] for k in keep)
        self._e_len.extend(self._e_len[k] for k in keep)
        self._e_child.extend(self._e_child[k] for k in keep)
        self._n_start[node] = len(self._e_first) - (count - 1)
        self._n_count[node] = count - 1

    def _edge_label(self, slot: int) -> bytes:
        off = self._e_off[slot]
        return bytes(self._labels[off:off + self._e_len[slot]])

    # ------------------------------------------------------------------ dunder
    def __len__(self) -> int:
        return self._size

    def __contains__(self, key: bytes) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __getitem__(self, key: bytes):
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key: bytes, value) -> None:
        self.insert(key, value)

    def __delitem__(self, key: bytes) -> None:
        self.delete(key)

    def __iter__(self) -> Iterator[bytes]:
        return self.keys()

    # ------------------------------------------------------------------ lookup
    def get(self, key: bytes, default=None):
        key = bytes(key)
        node = 0
        i, n = 0, len(key)
        labels = self._labels
        while i < n:
            slot = self._find_edge(node, key[i])
            if slot < 0:
                return default
            off = self._e_off[slot]
            ln = self._e_len[slot]
            if ln > n - i or labels[off:off + ln] != key[i:i + ln]:
                return default
            node = self._e_child[slot]
            i += ln
        value = self._values[node]
        return default if value is _MISSING else value

    # ------------------------------------------------------------------ insert
    def insert(self, key: bytes, value) -> None:
        key = bytes(key)
        node = 0
        i, n = 0, len(key)
        labels = self._labels
        while i < n:
            slot = self._find_edge(node, key[i])
            if slot < 0:
                leaf = self._new_node()
                off = len(labels)
                labels += key[i:]
                self._add_edge(node, key[i], off, n - i, leaf)
                self._values[leaf] = value
                self._size += 1
                return
            off = self._e_off[slot]
            ln = self._e_len[slot]
            # longest common prefix of the edge label and key[i:]
            limit = ln if ln < n - i else n - i
            j = 0
            while j < limit and labels[off + j] == key[i + j]:
                j += 1
            if j == ln:
                node = self._e_child[slot]
                i += j
                continue
            # split the edge at offset j: head stays in place, the tail is
            # already in the arena right after it, so no bytes are copied
            child = self._e_child[slot]
            mid = self._new_node()
            self._add_edge(mid, labels[off + j], off + j, ln - j, child)
            self._e_len[slot] = j
            self._e_child[slot] = mid
            if i + j == n:
                self._values[mid] = value
            else:
                leaf = self._new_node()
                off2 = len(labels)
                labels += key[i + j:]
                self._add_edge(mid, key[i + j], off2, n - i - j, leaf)
                self._values[leaf] = value
            self._size += 1
            return
        if self._values[node] is _MISSING:
            self._size += 1
        self._values[node] = value

    # ------------------------------------------------------------------ delete
    def delete(self, key: bytes) -> None:
        key = bytes(key)
        path = []  # (node, edge slot) along the way
        node = 0
        i, n = 0, len(key)
        labels = self._labels
        while i < n:
            slot = self._find_edge(node, key[i])
            if slot < 0:
                raise KeyError(key)
            off = self._e_off[slot]
            ln = self._e_len[slot]
            if ln > n - i or labels[off:off + ln] != key[i:i + ln]:
                raise KeyError(key)
            path.append((node, slot))
            node = self._e_child[slot]
            i += ln
        if self._values[node] is _MISSING:
            raise KeyError(key)
        self._values[node] = _MISSING
        self._size -= 1
        # Re-compress: drop value-less leaves, merge single-child chains.
        while path:
            parent, slot = path.pop()
            child = self._e_child[slot]
            if self._values[child] is not _MISSING or self._n_count[child] >= 2:
                break
            if self._n_count[child] == 1:
                # merge the child's only edge into the parent edge
                ce = self._n_start[child]
                off = self._e_off[slot]
                ln = self._e_len[slot]
                coff = self._e_off[ce]
                clen = self._e_len[ce]
                if coff == off + ln:
                    # labels are adjacent in the arena: just extend
                    self._e_len[slot] = ln + clen
                else:
                    new_off = len(labels)
                    labels += labels[off:off + ln]
                    labels += labels[coff:coff + clen]
                    self._e_off[slot] = new_off
                    self._e_len[slot] = ln + clen
                self._e_child[slot] = self._e_child[ce]
                break
            # value-less leaf: remove the edge, parent may now need merging
            self._remove_edge(parent, slot)

    # --------------------------------------------------------------- iteration
    def items(
        self,
        start: Optional[bytes] = None,
        stop: Optional[bytes] = None,
    ) -> Iterator[Tuple[bytes, object]]:
        """Yield ``(key, value)`` in lexicographic order.

        ``start`` is inclusive, ``stop`` is exclusive; either may be ``None``.
        """
        if start is not None:
            start = bytes(start)
        if stop is not None:
            stop = bytes(stop)
        stack: list[tuple[int, bytes]] = [(0, b"")]
        while stack:
            node, prefix = stack.pop()
            value = self._values[node]
            if value is not _MISSING and (
                (start is None or prefix >= start)
                and (stop is None or prefix < stop)
            ):
                yield prefix, value
            begin = self._n_start[node]
            count = self._n_count[node]
            order = sorted(
                range(begin, begin + count),
                key=self._e_first.__getitem__,
                reverse=True,
            )
            for slot in order:
                child = self._e_child[slot]
                stack.append((child, prefix + self._edge_label(slot)))

    def keys(self, start: Optional[bytes] = None, stop: Optional[bytes] = None):
        for key, _ in self.items(start, stop):
            yield key

    def values(self, start: Optional[bytes] = None, stop: Optional[bytes] = None):
        for _, value in self.items(start, stop):
            yield value

    def items_with_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, object]]:
        """Yield ``(key, value)`` for all keys starting with ``prefix``."""
        prefix = bytes(prefix)
        node = 0
        path = b""
        i, n = 0, len(prefix)
        labels = self._labels
        while i < n:
            slot = self._find_edge(node, prefix[i])
            if slot < 0:
                return
            off = self._e_off[slot]
            ln = self._e_len[slot]
            remaining = n - i
            if remaining < ln:
                # prefix ends in the middle of an edge: the whole subtree
                # below matches, and keys continue with the full label
                if labels[off:off + remaining] != prefix[i:]:
                    return
                node = self._e_child[slot]
                path += labels[off:off + ln]
                break
            if labels[off:off + ln] != prefix[i:i + ln]:
                return
            node = self._e_child[slot]
            path += labels[off:off + ln]
            i += ln
        else:
            path = prefix
        stack: list[tuple[int, bytes]] = [(node, path)]
        while stack:
            current, cur_path = stack.pop()
            value = self._values[current]
            if value is not _MISSING:
                yield cur_path, value
            begin = self._n_start[current]
            count = self._n_count[current]
            order = sorted(
                range(begin, begin + count),
                key=self._e_first.__getitem__,
                reverse=True,
            )
            for slot in order:
                child = self._e_child[slot]
                stack.append((child, cur_path + self._edge_label(slot)))

    def keys_with_prefix(self, prefix: bytes) -> Iterator[bytes]:
        for key, _ in self.items_with_prefix(prefix):
            yield key

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        """Structural statistics (used for space accounting)."""
        nodes = len(self._values)
        edges = 0
        label_bytes = 0
        for node in range(nodes):
            begin = self._n_start[node]
            count = self._n_count[node]
            edges += count
            for slot in range(begin, begin + count):
                label_bytes += self._e_len[slot]
        return {
            "nodes": nodes,
            "edges": edges,
            "label_bytes": label_bytes,
            "arena_bytes": len(self._labels),
        }

    def memory_bytes(self) -> int:
        """Bytes retained by the tree structure (excluding value objects)."""
        total = sys.getsizeof(self._labels)
        for arr in (
            self._e_first, self._e_off, self._e_len,
            self._e_child, self._n_start, self._n_count,
        ):
            total += arr.buffer_info()[1] * arr.itemsize
            total += sys.getsizeof(arr) - arr.buffer_info()[1] * arr.itemsize
        total += sys.getsizeof(self._values)
        return total
