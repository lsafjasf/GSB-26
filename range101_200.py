"""Inclusive integer interval 101-200 (Python 3, stdlib only).

The task specification is the bare inclusive range ``101-200``.
This module treats that as a request to work with the inclusive
integer interval [101, 200]: it exposes the bounds, a lazy view over
all values in the interval, common interval queries (membership,
size, containment, overlap, adjacency, intersection, union hull,
enumeration) and a command-line entry point that prints one value
per line.

Run the self-tests with::

    python3 range101_200.py --test
"""

from __future__ import annotations

import sys
import unittest
from typing import Iterator, Optional

LOWER_BOUND = 101
UPPER_BOUND = 200


class InclusiveRange:
    """A closed integer interval [lo, hi] (both ends included)."""

    __slots__ = ("lo", "hi")

    def __init__(self, lo: int, hi: int) -> None:
        if not isinstance(lo, int) or isinstance(lo, bool):
            raise TypeError("lo must be an int")
        if not isinstance(hi, int) or isinstance(hi, bool):
            raise TypeError("hi must be an int")
        if lo > hi:
            raise ValueError(f"empty interval: lo {lo} > hi {hi}")
        self.lo = lo
        self.hi = hi

    def __repr__(self) -> str:
        return f"InclusiveRange({self.lo}, {self.hi})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, InclusiveRange)
            and self.lo == other.lo
            and self.hi == other.hi
        )

    def __hash__(self) -> int:
        return hash((self.lo, self.hi))

    def __iter__(self) -> Iterator[int]:
        return iter(range(self.lo, self.hi + 1))

    def __len__(self) -> int:
        return self.hi - self.lo + 1

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        return self.lo <= value <= self.hi

    def values(self) -> range:
        """All integers in the interval, ascending."""
        return range(self.lo, self.hi + 1)

    def nth(self, index: int) -> int:
        """0-based value from the start (negative counts from the end)."""
        if index >= 0:
            value = self.lo + index
        else:
            value = self.hi + 1 + index
        if value not in self:
            raise IndexError("index out of range")
        return value

    def sum(self) -> int:
        """Arithmetic-series sum of every integer in the interval."""
        return (self.lo + self.hi) * len(self) // 2

    def contains_range(self, other: "InclusiveRange") -> bool:
        return self.lo <= other.lo and other.hi <= self.hi

    def overlaps(self, other: "InclusiveRange") -> bool:
        return not (self.hi < other.lo or other.hi < self.lo)

    def is_adjacent_to(self, other: "InclusiveRange") -> bool:
        return self.hi + 1 == other.lo or other.hi + 1 == self.lo

    def intersection(self, other: "InclusiveRange") -> Optional["InclusiveRange"]:
        lo = max(self.lo, other.lo)
        hi = min(self.hi, other.hi)
        return InclusiveRange(lo, hi) if lo <= hi else None

    def union_hull(self, other: "InclusiveRange") -> "InclusiveRange":
        """Smallest interval covering both (works even with a gap)."""
        return InclusiveRange(min(self.lo, other.lo), max(self.hi, other.hi))


#: The interval named by the task.
RANGE_101_200 = InclusiveRange(LOWER_BOUND, UPPER_BOUND)


def numbers() -> range:
    """Every integer from 101 through 200 inclusive (100 values)."""
    return RANGE_101_200.values()


class TestRange101To200(unittest.TestCase):
    def test_bounds_and_size(self):
        self.assertEqual(RANGE_101_200.lo, 101)
        self.assertEqual(RANGE_101_200.hi, 200)
        self.assertEqual(len(RANGE_101_200), 100)

    def test_endpoints_included(self):
        values = list(numbers())
        self.assertEqual(values[0], 101)
        self.assertEqual(values[-1], 200)
        self.assertEqual(len(values), 100)
        self.assertEqual(len(set(values)), 100)

    def test_membership(self):
        self.assertIn(101, RANGE_101_200)
        self.assertIn(150, RANGE_101_200)
        self.assertIn(200, RANGE_101_200)
        self.assertNotIn(100, RANGE_101_200)
        self.assertNotIn(201, RANGE_101_200)
        self.assertNotIn("150", RANGE_101_200)

    def test_sum(self):
        expected = sum(range(101, 201))
        self.assertEqual(RANGE_101_200.sum(), expected)
        self.assertEqual(expected, (101 + 200) * 100 // 2)

    def test_nth(self):
        self.assertEqual(RANGE_101_200.nth(0), 101)
        self.assertEqual(RANGE_101_200.nth(99), 200)
        self.assertEqual(RANGE_101_200.nth(-1), 200)
        self.assertEqual(RANGE_101_200.nth(-100), 101)
        with self.assertRaises(IndexError):
            RANGE_101_200.nth(100)

    def test_validation(self):
        with self.assertRaises(ValueError):
            InclusiveRange(2, 1)
        with self.assertRaises(TypeError):
            InclusiveRange(True, 5)

    def test_set_operations(self):
        middle = InclusiveRange(150, 250)
        self.assertTrue(RANGE_101_200.overlaps(middle))
        self.assertEqual(RANGE_101_200.intersection(middle), InclusiveRange(150, 200))
        self.assertEqual(RANGE_101_200.union_hull(middle), InclusiveRange(101, 250))
        self.assertTrue(InclusiveRange(90, 100).is_adjacent_to(RANGE_101_200))
        self.assertTrue(RANGE_101_200.contains_range(InclusiveRange(120, 130)))
        self.assertIsNone(RANGE_101_200.intersection(InclusiveRange(201, 300)))


def main(argv: Optional[list] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--test":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRange101To200)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    for value in numbers():
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
