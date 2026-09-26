"""RadixStore: an ordered key-value store with prefix compression.

Keys are arbitrary ``bytes`` (binary safe, including ``\\x00`` and empty
keys); values are arbitrary Python objects.  Shared key prefixes are stored
exactly once on compressed edges, so memory scales with the *entropy* of
the key set rather than the total key bytes.

Supported operations:

- ``insert(key, value)`` / ``store[key] = value``
- ``get(key, default)`` / ``store[key]`` / ``key in store``
- ``delete(key)`` / ``del store[key]``
- ``items()`` / ``keys()`` / ``values()`` -- full scan in lexicographic order
- ``items_with_prefix(prefix)`` (plus keys_/values_ variants) -- prefix query
- ``len(store)`` -- number of live keys

Standard library only.
"""

__all__ = ["RadixStore"]

_MISSING = object()  # sentinel: node carries no value


class _Node:
    __slots__ = ("children", "value")

    def __init__(self):
        # first byte of the outgoing edge label -> (edge label, child node)
        self.children = {}
        self.value = _MISSING


def _coerce_key(key):
    if isinstance(key, bytes):
        return key
    if isinstance(key, (bytearray, memoryview)):
        return bytes(key)
    raise TypeError("keys must be bytes-like, got %r" % type(key).__name__)


class RadixStore:
    """Compressed radix tree mapping ``bytes`` keys to values."""

    def __init__(self, items=()):
        self._root = _Node()
        self._size = 0
        for key, value in items:
            self.insert(key, value)

    # ------------------------------------------------------------------ #
    # core API
    # ------------------------------------------------------------------ #

    def __len__(self):
        return self._size

    def insert(self, key, value=True):
        """Insert or overwrite ``key`` -> ``value``."""
        key = _coerce_key(key)
        node = self._root
        i = 0
        n = len(key)
        while i < n:
            first = key[i]
            entry = node.children.get(first)
            if entry is None:
                leaf = _Node()
                leaf.value = value
                node.children[first] = (key[i:], leaf)
                self._size += 1
                return
            label, child = entry
            # length of the common prefix of the edge label and key[i:]
            common = 0
            limit = min(len(label), n - i)
            while common < limit and label[common] == key[i + common]:
                common += 1
            if common == len(label):
                # edge fully matched; descend
                node = child
                i += common
                continue
            # split the edge at `common`
            mid = _Node()
            rest = label[common:]
            mid.children[rest[0]] = (rest, child)
            node.children[first] = (label[:common], mid)
            if i + common == n:
                # the new key ends exactly at the split point
                mid.value = value
            else:
                leaf = _Node()
                leaf.value = value
                rem = key[i + common:]
                mid.children[rem[0]] = (rem, leaf)
            self._size += 1
            return
        # key exhausted exactly at `node`
        if node.value is _MISSING:
            self._size += 1
        node.value = value

    def _find_node(self, key):
        node = self._root
        i = 0
        n = len(key)
        while i < n:
            entry = node.children.get(key[i])
            if entry is None:
                return None
            label, child = entry
            if key[i:i + len(label)] != label:
                return None
            node = child
            i += len(label)
        return node

    def get(self, key, default=None):
        node = self._find_node(_coerce_key(key))
        if node is None or node.value is _MISSING:
            return default
        return node.value

    def __getitem__(self, key):
        node = self._find_node(_coerce_key(key))
        if node is None or node.value is _MISSING:
            raise KeyError(key)
        return node.value

    def __setitem__(self, key, value):
        self.insert(key, value)

    def __contains__(self, key):
        try:
            key = _coerce_key(key)
        except TypeError:
            return False
        node = self._find_node(key)
        return node is not None and node.value is not _MISSING

    def delete(self, key):
        """Remove ``key``; raises ``KeyError`` if absent.

        After removal, nodes that lost their value and are left with a
        single child are merged back into that child, keeping the tree
        fully compressed.
        """
        key = _coerce_key(key)
        node = self._root
        i = 0
        n = len(key)
        stack = []  # (parent, first byte, edge label, node)
        while True:
            if i == n:
                if node.value is _MISSING:
                    raise KeyError(key)
                node.value = _MISSING
                self._size -= 1
                break
            first = key[i]
            entry = node.children.get(first)
            if entry is None:
                raise KeyError(key)
            label, child = entry
            if key[i:i + len(label)] != label:
                raise KeyError(key)
            stack.append((node, first, label, child))
            node = child
            i += len(label)

        # 1) drop edges to nodes that became empty and value-less
        while stack:
            parent, first, label, node = stack[-1]
            if node.value is _MISSING and not node.children:
                del parent.children[first]
                stack.pop()
            else:
                break
        # 2) merge a value-less single-child node into its only child
        if stack:
            parent, first, label, node = stack[-1]
            if node.value is _MISSING and len(node.children) == 1:
                (child_first, (child_label, grandchild)), = node.children.items()
                parent.children[first] = (label + child_label, grandchild)

    def __delitem__(self, key):
        self.delete(key)

    # ------------------------------------------------------------------ #
    # ordered traversal / prefix queries
    # ------------------------------------------------------------------ #

    @staticmethod
    def _iter_from(node, base):
        """Yield (key, value) under ``node`` in lexicographic order.

        ``base`` is the key prefix accumulated on the path to ``node``.
        Iterative (explicit stack) so arbitrarily long keys are safe.
        """
        stack = [(base, node)]
        while stack:
            base, node = stack.pop()
            if node.value is not _MISSING:
                yield base, node.value
            # children have distinct first bytes; pushing in reverse
            # sorted order makes the pop order ascending
            for first in sorted(node.children, reverse=True):
                label, child = node.children[first]
                stack.append((base + label, child))

    def items(self):
        """All ``(key, value)`` pairs in lexicographic key order."""
        return self._iter_from(self._root, b"")

    def keys(self):
        for key, _ in self.items():
            yield key

    def values(self):
        for _, value in self.items():
            yield value

    def _subtree(self, prefix):
        """Return (base, node) covering every key starting with ``prefix``."""
        node = self._root
        i = 0
        n = len(prefix)
        while i < n:
            entry = node.children.get(prefix[i])
            if entry is None:
                return None
            label, child = entry
            remaining = n - i
            if remaining < len(label):
                # prefix ends in the middle of an edge
                if label[:remaining] == prefix[i:]:
                    return prefix[:i] + label, child
                return None
            if label != prefix[i:i + len(label)]:
                return None
            i += len(label)
            node = child
        return prefix, node

    def items_with_prefix(self, prefix):
        """All ``(key, value)`` pairs whose key starts with ``prefix``,
        in lexicographic order."""
        prefix = _coerce_key(prefix)
        found = self._subtree(prefix)
        if found is None:
            return iter(())
        base, node = found
        return self._iter_from(node, base)

    def keys_with_prefix(self, prefix):
        for key, _ in self.items_with_prefix(prefix):
            yield key

    def values_with_prefix(self, prefix):
        for _, value in self.items_with_prefix(prefix):
            yield value

    # ------------------------------------------------------------------ #
    # introspection (used by the benchmark)
    # ------------------------------------------------------------------ #

    def stats(self):
        """Structural statistics: node/edge counts and stored label bytes."""
        nodes = 0
        edges = 0
        label_bytes = 0
        stack = [self._root]
        while stack:
            node = stack.pop()
            nodes += 1
            for label, child in node.children.values():
                edges += 1
                label_bytes += len(label)
                stack.append(child)
        return {
            "size": self._size,
            "nodes": nodes,
            "edges": edges,
            "label_bytes": label_bytes,
        }
