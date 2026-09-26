"""Tests for RadixTree, including differential testing against a naive
uncompressed model (plain dict + sorted())."""

import random
import sys
import unittest

from radix_tree import RadixTree, _MISSING


def naive_items(model: dict, start=None, stop=None):
    for key in sorted(model):
        if (start is None or key >= start) and (stop is None or key < stop):
            yield key, model[key]


def naive_prefix(model: dict, prefix):
    for key in sorted(model):
        if key.startswith(prefix):
            yield key, model[key]


def check_invariants(testcase, tree):
    """Every non-root node must carry a value or have != 1 children;
    edge labels must be non-empty and consistent with the parent map."""
    valued = 0
    stack = [0]
    while stack:
        node = stack.pop()
        if tree._values[node] is not _MISSING:
            valued += 1
        elif node != 0:
            testcase.assertNotEqual(
                tree._n_count[node], 1,
                "uncompressed single-child chain found",
            )
        begin = tree._n_start[node]
        count = tree._n_count[node]
        firsts = set()
        for slot in range(begin, begin + count):
            testcase.assertTrue(tree._e_len[slot] > 0)
            off = tree._e_off[slot]
            testcase.assertEqual(tree._labels[off], tree._e_first[slot])
            testcase.assertNotIn(tree._e_first[slot], firsts)
            firsts.add(tree._e_first[slot])
            stack.append(tree._e_child[slot])
    testcase.assertEqual(valued, len(tree))


class TestBasic(unittest.TestCase):
    def test_empty(self):
        t = RadixTree()
        self.assertEqual(len(t), 0)
        self.assertEqual(list(t.items()), [])
        self.assertEqual(list(t.items_with_prefix(b"")), [])
        self.assertEqual(list(t.items_with_prefix(b"x")), [])
        self.assertFalse(b"x" in t)
        self.assertIsNone(t.get(b"x"))
        with self.assertRaises(KeyError):
            t.delete(b"x")
        with self.assertRaises(KeyError):
            t[b"x"]

    def test_single_key(self):
        t = RadixTree()
        t.insert(b"hello", 1)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[b"hello"], 1)
        self.assertEqual(list(t.items()), [(b"hello", 1)])
        t.insert(b"hello", 2)  # overwrite, size unchanged
        self.assertEqual(len(t), 1)
        self.assertEqual(t[b"hello"], 2)
        t.delete(b"hello")
        self.assertEqual(len(t), 0)
        self.assertEqual(list(t.items()), [])
        check_invariants(self, t)

    def test_empty_key(self):
        t = RadixTree()
        t.insert(b"", "empty")
        t.insert(b"a", "a")
        self.assertEqual(t[b""], "empty")
        self.assertEqual(list(t.keys()), [b"", b"a"])
        t.delete(b"")
        self.assertNotIn(b"", t)
        check_invariants(self, t)

    def test_key_is_prefix_of_another(self):
        t = RadixTree()
        for i, k in enumerate([b"a", b"ab", b"abc", b"abcd", b"b", b"abx"]):
            t.insert(k, i)
        self.assertEqual(len(t), 6)
        self.assertEqual([k for k, _ in t.items()],
                         [b"a", b"ab", b"abc", b"abcd", b"abx", b"b"])
        self.assertEqual([k for k, _ in t.items_with_prefix(b"ab")],
                         [b"ab", b"abc", b"abcd", b"abx"])
        # delete the middle of a prefix chain
        t.delete(b"ab")
        self.assertNotIn(b"ab", t)
        self.assertEqual(t[b"abc"], 2)
        t.delete(b"abc")
        t.delete(b"abcd")
        self.assertEqual([k for k, _ in t.items()], [b"a", b"abx", b"b"])
        check_invariants(self, t)

    def test_binary_keys(self):
        t = RadixTree()
        keys = [bytes([i]) for i in range(256)]
        keys += [b"\x00", b"\x00\x00", b"\xff" * 8, b"a\x00b", b"a\x00",
                 bytes(range(256))]
        for i, k in enumerate(keys):
            t.insert(k, i)
        self.assertEqual(len(t), len(set(keys)))
        expected = sorted(set(keys))
        self.assertEqual([k for k, _ in t.items()], expected)
        for k in keys:
            self.assertIn(k, t)
        self.assertEqual([k for k, _ in t.items_with_prefix(b"\x00")],
                         [k for k in expected if k.startswith(b"\x00")])
        check_invariants(self, t)

    def test_long_keys(self):
        t = RadixTree()
        base = b"p" * 5000
        keys = [base + bytes([i]) * (i + 1) for i in range(200)]
        keys.append(b"q" * 10000)
        for i, k in enumerate(keys):
            t.insert(k, i)
        self.assertEqual([k for k, _ in t.items()], sorted(keys))
        for i, k in enumerate(keys):
            self.assertEqual(t[k], i)
        # delete half, verify the rest
        for k in keys[::2]:
            t.delete(k)
        remaining = sorted(keys[1::2])
        self.assertEqual([k for k, _ in t.items()], remaining)
        check_invariants(self, t)

    def test_range_query(self):
        t = RadixTree()
        model = {}
        rng = random.Random(7)
        for _ in range(2000):
            k = bytes(rng.randrange(0, 5) for _ in range(rng.randrange(0, 6)))
            model[k] = len(k)
            t.insert(k, len(k))
        for _ in range(500):
            a = bytes(rng.randrange(0, 5) for _ in range(rng.randrange(0, 4)))
            b = bytes(rng.randrange(0, 5) for _ in range(rng.randrange(0, 4)))
            start, stop = min(a, b), max(a, b)
            self.assertEqual(list(t.items(start, stop)),
                             list(naive_items(model, start, stop)))
        self.assertEqual(list(t.items()), list(naive_items(model)))


class TestDifferential(unittest.TestCase):
    """Random operation sequences compared against the naive model."""

    def run_model(self, seed, ops, key_space):
        rng = random.Random(seed)
        t = RadixTree()
        model = {}
        for step in range(ops):
            op = rng.random()
            key = key_space[rng.randrange(len(key_space))]
            if op < 0.45:
                value = rng.randrange(1 << 30)
                t.insert(key, value)
                model[key] = value
            elif op < 0.7:
                if key in model:
                    t.delete(key)
                    del model[key]
                else:
                    with self.assertRaises(KeyError):
                        t.delete(key)
            elif op < 0.85:
                expected = model.get(key, _MISSING)
                got = t.get(key, _MISSING)
                self.assertEqual(got, expected)
            else:
                prefix = key[: rng.randrange(len(key) + 1)]
                self.assertEqual(list(t.items_with_prefix(prefix)),
                                 list(naive_prefix(model, prefix)))
            if step % 200 == 0:
                self.assertEqual(len(t), len(model))
                self.assertEqual(list(t.items()), list(naive_items(model)))
        self.assertEqual(len(t), len(model))
        self.assertEqual(list(t.items()), list(naive_items(model)))
        check_invariants(self, t)

    def test_random_binary_keys(self):
        rng = random.Random(42)
        key_space = set()
        while len(key_space) < 400:
            n = rng.randrange(0, 12)
            key_space.add(bytes(rng.randrange(256) for _ in range(n)))
        self.run_model(seed=1, ops=8000, key_space=sorted(key_space))

    def test_shared_prefix_keys(self):
        rng = random.Random(43)
        key_space = set()
        while len(key_space) < 500:
            tenant = rng.randrange(4)
            key_space.add(
                b"/objects/tenant-%d/" % tenant
                + b"%04d" % rng.randrange(2000)
            )
        self.run_model(seed=2, ops=8000, key_space=sorted(key_space))

    def test_many_keys_ordered_iteration(self):
        # >10k keys: full ordered traversal must match the naive model.
        rng = random.Random(44)
        model = {}
        t = RadixTree()
        for i in range(20000):
            k = b"user/%05d/profile/%s" % (
                rng.randrange(30000),
                bytes([rng.randrange(256) for _ in range(4)]),
            )
            model[k] = i
            t.insert(k, i)
        self.assertEqual(len(t), len(model))
        self.assertEqual(list(t.items()), list(naive_items(model)))
        # delete a third of them, re-verify
        for k in list(model)[::3]:
            t.delete(k)
            del model[k]
        self.assertEqual(list(t.items()), list(naive_items(model)))
        check_invariants(self, t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
