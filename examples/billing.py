"""Example module under mutation testing.

Contains, on purpose:
  * well tested functions (all their mutants are killed),
  * a weakly tested guard in `discount` (its mutants survive),
  * genuinely equivalent mutants (documented in equivalents.json),
  * mutants that cause an infinite loop in `count_down` (timeout).
"""


def clamp01(x):
    if x < 0:
        return 0
    if x > 1:
        return 1
    return x


def is_even(n):
    return n % 2 == 0


def in_range(x, low, high):
    return low <= x and x <= high


def triangle_area(base, height):
    return base * height / 2


def normalize(text):
    text = text.strip()
    if not text:
        return ""
    return text


def sort_names(names):
    result = sorted(names)
    result = list(result)
    return result


def count_down(n):
    total = 0
    while n > 0:
        total += n
        n -= 1
    return total


def discount(price, percent):
    if percent > 100:
        raise ValueError("percent too large")
    return price * (1 - percent / 100)
