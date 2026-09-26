"""Utilities for the inclusive integer range 101-200 (Python 3, stdlib only).

The task specification is the bare inclusive range ``101-200``. This
module interprets that as the closed integer interval [101, 200] and
provides the values, basic aggregate queries (count, sum, membership,
even/odd splits, primes) and a small CLI.

Usage::

    python3 range101_200.py            # print 101..200, one per line
    python3 range101_200.py --stats    # print summary statistics
    python3 range101_200.py --test     # run the self-tests
"""

from __future__ import annotations

import sys
import unittest

START = 101
END = 200


def numbers(start: int = START, end: int = END) -> range:
    """All integers in the inclusive interval [start, end]."""
    if start > end:
        raise ValueError(f"empty range: {start} > {end}")
    return range(start, end + 1)


def contains(value: int, start: int = START, end: int = END) -> bool:
    """True when ``value`` lies inside the inclusive interval."""
    return start <= value <= end


def count(start: int = START, end: int = END) -> int:
    """How many integers the inclusive interval holds."""
    return len(numbers(start, end))


def total(start: int = START, end: int = END) -> int:
    """Sum of every integer in the interval (arithmetic series)."""
    n = count(start, end)
    return (start + end) * n // 2


def evens(start: int = START, end: int = END) -> range:
    """Even values in the interval, ascending."""
    first = start if start % 2 == 0 else start + 1
    return range(first, end + 1, 2)


def odds(start: int = START, end: int = END) -> range:
    """Odd values in the interval, ascending."""
    first = start if start % 2 == 1 else start + 1
    return range(first, end + 1, 2)


def is_prime(value: int) -> bool:
    """Naive primality test, plenty fast for this interval."""
    if value < 2:
        return False
    if value < 4:
        return True
    if value % 2 == 0:
        return False
    factor = 3
    while factor * factor <= value:
        if value % factor == 0:
            return False
        factor += 2
    return True


def primes(start: int = START, end: int = END) -> list:
    """Prime numbers inside the inclusive interval."""
    return [v for v in numbers(start, end) if is_prime(v)]


def stats(start: int = START, end: int = END) -> dict:
    """Summary statistics for the interval."""
    values = numbers(start, end)
    prime_list = primes(start, end)
    return {
        "start": start,
        "end": end,
        "count": len(values),
        "sum": total(start, end),
        "min": start,
        "max": end,
        "evens": len(evens(start, end)),
        "odds": len(odds(start, end)),
        "primes": prime_list,
        "prime_count": len(prime_list),
    }


class TestRange101To200(unittest.TestCase):
    def test_bounds(self):
        values = list(numbers())
        self.assertEqual(values[0], 101)
        self.assertEqual(values[-1], 200)
        self.assertEqual(len(values), 100)

    def test_contains(self):
        self.assertTrue(contains(101))
        self.assertTrue(contains(200))
        self.assertTrue(contains(150))
        self.assertFalse(contains(100))
        self.assertFalse(contains(201))

    def test_count_and_sum(self):
        self.assertEqual(count(), 100)
        self.assertEqual(total(), sum(range(101, 201)))
        self.assertEqual(total(), 15050)

    def test_empty_range_rejected(self):
        with self.assertRaises(ValueError):
            numbers(200, 101)

    def test_evens_odds_partition(self):
        even_list = list(evens())
        odd_list = list(odds())
        self.assertEqual(len(even_list) + len(odd_list), 100)
        self.assertTrue(all(v % 2 == 0 for v in even_list))
        self.assertTrue(all(v % 2 == 1 for v in odd_list))
        self.assertEqual(even_list[0], 102)
        self.assertEqual(odd_list[0], 101)

    def test_primes(self):
        expected = [101, 103, 107, 109, 113, 127, 131, 137, 139, 149,
                    151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199]
        self.assertEqual(primes(), expected)
        self.assertEqual(len(expected), 21)

    def test_stats_consistency(self):
        summary = stats()
        self.assertEqual(summary["count"], 100)
        self.assertEqual(summary["sum"], 15050)
        self.assertEqual(summary["evens"] + summary["odds"], 100)
        self.assertEqual(summary["prime_count"], len(summary["primes"]))


def main(argv: list) -> int:
    args = argv[1:]
    if args and args[0] == "--test":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRange101To200)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args and args[0] == "--stats":
        for key, value in stats().items():
            print(f"{key}: {value}")
        return 0
    for value in numbers():
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
