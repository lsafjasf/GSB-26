"""Test suite for examples/billing.py.

Deliberately strong everywhere except for `discount`, whose guard
(`percent > 100`) is never exercised -- its mutants must survive.
"""
import unittest

from billing import (clamp01, count_down, discount, in_range, is_even,
                     normalize, sort_names, triangle_area)


class Clamp01Test(unittest.TestCase):
    def test_below(self):
        self.assertEqual(clamp01(-0.5), 0)

    def test_above(self):
        self.assertEqual(clamp01(1.5), 1)

    def test_inside(self):
        self.assertEqual(clamp01(0.5), 0.5)

    def test_boundaries(self):
        self.assertEqual(clamp01(0), 0)
        self.assertEqual(clamp01(1), 1)


class IsEvenTest(unittest.TestCase):
    def test_even(self):
        self.assertTrue(is_even(4))

    def test_odd(self):
        self.assertFalse(is_even(3))


class InRangeTest(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(in_range(2, 1, 3))

    def test_lower_boundary(self):
        self.assertTrue(in_range(1, 1, 3))

    def test_upper_boundary(self):
        self.assertTrue(in_range(3, 1, 3))

    def test_below(self):
        self.assertFalse(in_range(0, 1, 3))

    def test_above(self):
        self.assertFalse(in_range(5, 1, 3))


class TriangleAreaTest(unittest.TestCase):
    def test_area(self):
        self.assertEqual(triangle_area(2, 4), 4)


class NormalizeTest(unittest.TestCase):
    def test_strips(self):
        self.assertEqual(normalize("  hi  "), "hi")

    def test_blank(self):
        self.assertEqual(normalize("   "), "")


class SortNamesTest(unittest.TestCase):
    def test_sorted(self):
        self.assertEqual(sort_names(["bob", "al", "cy"]), ["al", "bob", "cy"])

    def test_returns_new_list(self):
        names = ["b", "a"]
        self.assertIsNot(sort_names(names), names)


class CountDownTest(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(count_down(5), 15)

    def test_zero(self):
        self.assertEqual(count_down(0), 0)


class DiscountTest(unittest.TestCase):
    # NOTE: intentionally weak -- the `percent > 100` guard is never tested.
    def test_half_off(self):
        self.assertEqual(discount(200, 50), 100)


if __name__ == "__main__":
    unittest.main()
