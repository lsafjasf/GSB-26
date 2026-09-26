"""Naive point-wise reference implementation with the exact same API and
bounds semantics as segtree.SegmentTree. Used as the oracle for stress tests
and as the baseline for benchmarks."""


class NaiveArray:
    __slots__ = ("_a",)

    def __init__(self, data):
        self._a = list(data)
        if not self._a:
            raise ValueError("data must be non-empty")

    def __len__(self):
        return len(self._a)

    def _check(self, l, r):
        if not (0 <= l <= r <= len(self._a)):
            raise IndexError(
                f"range [{l}, {r}) out of bounds for length {len(self._a)}"
            )

    def range_add(self, l, r, v):
        self._check(l, r)
        a = self._a
        for i in range(l, r):
            a[i] += v

    def range_assign(self, l, r, v):
        self._check(l, r)
        a = self._a
        for i in range(l, r):
            a[i] = v

    def range_sum(self, l, r):
        self._check(l, r)
        return sum(self._a[l:r])

    def range_max(self, l, r):
        self._check(l, r)
        if l == r:
            raise ValueError("range_max of empty range")
        return max(self._a[l:r])

    def to_list(self):
        return list(self._a)
