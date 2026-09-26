"""PackedRadixStore: an immutable, memory-optimized compressed radix tree.

Built from a :class:`radix_store.RadixStore` (or any ``(key, value)``
iterable) and stored in flat ``array``/``bytes`` buffers instead of
per-node Python objects:

- all edge labels live in one shared ``bytes`` blob;
- edges are parallel arrays (first byte, label offset, label length,
  child node index), sorted by first byte for binary-search lookup;
- nodes are parallel arrays (edge range, value index).

This removes the per-node ``dict``/tuple overhead of the mutable tree and
is where the prefix compression shows up as real memory savings.  The
trade-off: no inserts/deletes -- rebuild instead.

Standard library only.
"""

from array import array

from radix_store import _MISSING, RadixStore, _coerce_key

__all__ = ["PackedRadixStore"]


class PackedRadixStore:
    """Read-only ordered key-value store with prefix compression."""

    __slots__ = ("_labels", "_first", "_off", "_len", "_child",
                 "_estart", "_ecount", "_vidx", "_values", "_size")

    def __init__(self):
        self._labels = b""        # all edge labels, concatenated
        self._first = b""         # first byte of each edge (sorted per node)
        self._off = array("q")    # edge -> offset of label in _labels
        self._len = array("i")    # edge -> label length
        self._child = array("i")  # edge -> child node index
        self._estart = array("i")  # node -> first edge index
        self._ecount = array("i")  # node -> number of edges
        self._vidx = array("i")    # node -> index into _values, -1 if none
        self._values = []
        self._size = 0

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #

    @classmethod
    def from_items(cls, items):
        return cls.from_store(RadixStore(items))

    @classmethod
    def from_store(cls, store):
        packed = cls()
        labels = bytearray()
        first = bytearray()
        off, length, child = array("q"), array("i"), array("i")
        estart, ecount, vidx = array("i"), array("i"), array("i")
        values = []
        size = 0

        # breadth-first numbering of nodes
        order = [store._root]
        node_index = {id(store._root): 0}
        i = 0
        while i < len(order):
            node = order[i]
            i += 1
            for f in sorted(node.children):
                c = node.children[f][1]
                node_index[id(c)] = len(order)
                order.append(c)

        for node in order:
            estart.append(len(first))
            count = 0
            for f in sorted(node.children):
                label, c = node.children[f]
                first.append(f)
                off.append(len(labels))
                labels += label
                length.append(len(label))
                child.append(node_index[id(c)])
                count += 1
            ecount.append(count)
            if node.value is _MISSING:
                vidx.append(-1)
            else:
                vidx.append(len(values))
                values.append(node.value)
                size += 1

        packed._labels = bytes(labels)
        packed._first = bytes(first)
        packed._off, packed._len, packed._child = off, length, child
        packed._estart, packed._ecount, packed._vidx = estart, ecount, vidx
        packed._values = values
        packed._size = size
        return packed

    # ------------------------------------------------------------------ #
    # lookups
    # ------------------------------------------------------------------ #

    def __len__(self):
        return self._size

    def _find_edge(self, node, byte):
        start = self._estart[node]
        end = start + self._ecount[node]
        lo, hi = start, end
        first = self._first
        while lo < hi:
            mid = (lo + hi) // 2
            if first[mid] < byte:
                lo = mid + 1
            else:
                hi = mid
        if lo < end and first[lo] == byte:
            return lo
        return -1

    def get(self, key, default=None):
        key = _coerce_key(key)
        node = 0
        i = 0
        n = len(key)
        labels = self._labels
        while i < n:
            e = self._find_edge(node, key[i])
            if e < 0:
                return default
            off = self._off[e]
            ln = self._len[e]
            if key[i:i + ln] != labels[off:off + ln]:
                return default
            i += ln
            node = self._child[e]
        v = self._vidx[node]
        return default if v < 0 else self._values[v]

    def __contains__(self, key):
        try:
            return self.get(key, _MISSING) is not _MISSING
        except TypeError:
            return False

    def __getitem__(self, key):
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    # ------------------------------------------------------------------ #
    # ordered traversal / prefix queries
    # ------------------------------------------------------------------ #

    def _iter_from(self, node, base):
        labels = self._labels
        stack = [(node, base)]
        while stack:
            node, base = stack.pop()
            v = self._vidx[node]
            if v >= 0:
                yield base, self._values[v]
            start = self._estart[node]
            end = start + self._ecount[node]
            for e in range(end - 1, start - 1, -1):
                off = self._off[e]
                stack.append((self._child[e],
                              base + labels[off:off + self._len[e]]))

    def items(self):
        """All ``(key, value)`` pairs in lexicographic key order."""
        return self._iter_from(0, b"")

    def keys(self):
        for key, _ in self.items():
            yield key

    def values(self):
        for _, value in self.items():
            yield value

    def items_with_prefix(self, prefix):
        """All ``(key, value)`` pairs whose key starts with ``prefix``,
        in lexicographic order."""
        prefix = _coerce_key(prefix)
        labels = self._labels
        node = 0
        i = 0
        n = len(prefix)
        while i < n:
            e = self._find_edge(node, prefix[i])
            if e < 0:
                return iter(())
            off = self._off[e]
            ln = self._len[e]
            remaining = n - i
            if remaining < ln:
                # prefix ends in the middle of an edge
                if labels[off:off + remaining] == prefix[i:]:
                    base = prefix[:i] + labels[off:off + ln]
                    return self._iter_from(self._child[e], base)
                return iter(())
            if labels[off:off + ln] != prefix[i:i + ln]:
                return iter(())
            i += ln
            node = self._child[e]
        return self._iter_from(node, prefix)

    def keys_with_prefix(self, prefix):
        for key, _ in self.items_with_prefix(prefix):
            yield key

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #

    def stats(self):
        buffers = (self._labels, self._first, self._off, self._len,
                   self._child, self._estart, self._ecount, self._vidx)
        nbytes = sum(b.buffer_info()[1] * b.itemsize if hasattr(b, "itemsize")
                     else len(b) for b in buffers)
        return {
            "size": self._size,
            "nodes": len(self._estart),
            "edges": len(self._first),
            "label_bytes": len(self._labels),
            "structure_bytes": nbytes,
        }
