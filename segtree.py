"""Lazy-propagation segment tree: range add / range assign / range sum / range max.

Semantics (all ranges are half-open [l, r), 0-based):
  - range_add(l, r, v):    a[i] += v for i in [l, r)
  - range_assign(l, r, v): a[i] = v  for i in [l, r)
  - range_sum(l, r):       sum of a[l:r]   (empty range -> 0)
  - range_max(l, r):       max of a[l:r]   (empty range -> ValueError)

Interleaving semantics: operations apply in call order. An assign overwrites
everything before it on that range (including earlier adds); adds after an
assign accumulate on top of the assigned value.

Lazy tags per node (node's own sum/max are always up to date):
  - _set[i]: pending assign not yet pushed to children (None = none)
  - _add[i]: pending add not yet pushed to children
  Tag composition: assign v  -> set=v, add=0 (discards older pending add);
                   add d     -> add+=d, or set+=d if a set is pending.
  Tags are pushed down exactly when a query/update must descend past a node
  whose children are stale (partial overlap). Full overlaps never push.

Bounds: 0 <= l <= r <= n is required; anything else raises IndexError.
Empty ranges (l == r) are no-ops for updates; range_sum returns 0 and
range_max raises ValueError on them.
"""

_NEG_INF = float("-inf")


class SegmentTree:
    __slots__ = ("_n", "_size", "_sum", "_max", "_add", "_set")

    def __init__(self, data):
        data = list(data)
        n = len(data)
        if n == 0:
            raise ValueError("data must be non-empty")
        size = 1
        while size < n:
            size <<= 1
        self._n = n
        self._size = size
        self._sum = [0] * (2 * size)
        self._max = [_NEG_INF] * (2 * size)
        self._add = [0] * (2 * size)
        self._set = [None] * (2 * size)
        base = size
        for i in range(n):
            self._sum[base + i] = data[i]
            self._max[base + i] = data[i]
        for i in range(size - 1, 0, -1):
            self._pull(i)

    def __len__(self):
        return self._n

    # ---------- public API ----------

    def range_add(self, l, r, v):
        self._check(l, r)
        if l < r:
            self._update_add(1, 0, self._size, l, r, v)

    def range_assign(self, l, r, v):
        self._check(l, r)
        if l < r:
            self._update_set(1, 0, self._size, l, r, v)

    def range_sum(self, l, r):
        self._check(l, r)
        if l == r:
            return 0
        return self._query_sum(1, 0, self._size, l, r)

    def range_max(self, l, r):
        self._check(l, r)
        if l == r:
            raise ValueError("range_max of empty range")
        return self._query_max(1, 0, self._size, l, r)

    def to_list(self):
        return [self.range_sum(i, i + 1) for i in range(self._n)]

    # ---------- internals ----------

    def _check(self, l, r):
        if not (0 <= l <= r <= self._n):
            raise IndexError(
                f"range [{l}, {r}) out of bounds for length {self._n}"
            )

    def _pull(self, i):
        self._sum[i] = self._sum[2 * i] + self._sum[2 * i + 1]
        a = self._max[2 * i]
        b = self._max[2 * i + 1]
        self._max[i] = a if a >= b else b

    def _apply_set(self, i, nl, nr, v):
        self._set[i] = v
        self._add[i] = 0
        self._sum[i] = v * (nr - nl)
        self._max[i] = v

    def _apply_add(self, i, nl, nr, v):
        if self._set[i] is not None:
            self._set[i] += v
        else:
            self._add[i] += v
        self._sum[i] += v * (nr - nl)
        self._max[i] += v

    def _push(self, i, nl, nr):
        mid = (nl + nr) >> 1
        s = self._set[i]
        if s is not None:
            self._apply_set(2 * i, nl, mid, s)
            self._apply_set(2 * i + 1, mid, nr, s)
            self._set[i] = None
        a = self._add[i]
        if a != 0:
            self._apply_add(2 * i, nl, mid, a)
            self._apply_add(2 * i + 1, mid, nr, a)
            self._add[i] = 0

    def _update_add(self, i, nl, nr, l, r, v):
        if r <= nl or nr <= l:
            return
        if l <= nl and nr <= r:
            self._apply_add(i, nl, nr, v)
            return
        self._push(i, nl, nr)
        mid = (nl + nr) >> 1
        self._update_add(2 * i, nl, mid, l, r, v)
        self._update_add(2 * i + 1, mid, nr, l, r, v)
        self._pull(i)

    def _update_set(self, i, nl, nr, l, r, v):
        if r <= nl or nr <= l:
            return
        if l <= nl and nr <= r:
            self._apply_set(i, nl, nr, v)
            return
        self._push(i, nl, nr)
        mid = (nl + nr) >> 1
        self._update_set(2 * i, nl, mid, l, r, v)
        self._update_set(2 * i + 1, mid, nr, l, r, v)
        self._pull(i)

    def _query_sum(self, i, nl, nr, l, r):
        if r <= nl or nr <= l:
            return 0
        if l <= nl and nr <= r:
            return self._sum[i]
        self._push(i, nl, nr)
        mid = (nl + nr) >> 1
        return (
            self._query_sum(2 * i, nl, mid, l, r)
            + self._query_sum(2 * i + 1, mid, nr, l, r)
        )

    def _query_max(self, i, nl, nr, l, r):
        if l <= nl and nr <= r:
            return self._max[i]
        self._push(i, nl, nr)
        mid = (nl + nr) >> 1
        best = _NEG_INF
        if l < mid:
            best = self._query_max(2 * i, nl, mid, l, r)
        if mid < r:
            right = self._query_max(2 * i + 1, mid, nr, l, r)
            if right > best:
                best = right
        return best
