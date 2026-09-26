"""Tests for RadixStore, including differential ("dui-pai") testing
against a naive uncompressed model (plain dict + sorted()).

Run:  python3 -m unittest -v test_radix_store
"""

import random
import unittest

from packed_store import PackedRadixStore
from radix_store import RadixStore


def model_items(model):
    """Naive uncompressed ordered view: sort full keys on every scan."""
    return sorted(model.items())


def model_prefix(model, prefix):
    return sorted((k, v) for k, v in model.items() if k.startswith(prefix))


class TestBasic(unittest.TestCase):
    def test_empty_store(self):
        s = RadixStore()
        self.assertEqual(len(s), 0)
        self.assertEqual(list(s.items()), [])
        self.assertEqual(list(s.items_with_prefix(b"anything")), [])
        self.assertEqual(list(s.items_with_prefix(b"")), [])
        self.assertNotIn(b"x", s)
        self.assertIsNone(s.get(b"x"))
        self.assertEqual(s.get(b"x", 42), 42)
        with self.assertRaises(KeyError):
            s.delete(b"x")
        with self.assertRaises(KeyError):
            s[b"x"]

    def test_single_key(self):
        s = RadixStore()
        s.insert(b"hello", 1)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[b"hello"], 1)
        self.assertEqual(list(s.items()), [(b"hello", 1)])
        # overwrite keeps size, updates value
        s.insert(b"hello", 2)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[b"hello"], 2)
        s.delete(b"hello")
        self.assertEqual(len(s), 0)
        self.assertEqual(list(s.items()), [])
        self.assertEqual(s.stats()["edges"], 0)

    def test_empty_key(self):
        s = RadixStore()
        s.insert(b"", "empty")
        s.insert(b"a", "a")
        self.assertEqual(len(s), 2)
        self.assertEqual(s[b""], "empty")
        self.assertEqual(list(s.items()), [(b"", "empty"), (b"a", "a")])
        s.delete(b"")
        self.assertNotIn(b"", s)
        self.assertEqual(list(s.items()), [(b"a", "a")])

    def test_key_is_prefix_of_other_key(self):
        keys = [b"a", b"ab", b"abc", b"abcdef"]
        for seq in (keys, list(reversed(keys))):
            s = RadixStore()
            for i, k in enumerate(seq):
                s.insert(k, i)
            self.assertEqual(len(s), 4)
            self.assertEqual([k for k, _ in s.items()], keys)
            for k in keys:
                self.assertIn(k, s)
            # delete a middle element; the rest must stay intact & ordered
            s.delete(b"ab")
            self.assertNotIn(b"ab", s)
            self.assertEqual([k for k, _ in s.items()], [b"a", b"abc", b"abcdef"])

    def test_delete_triggers_merge(self):
        s = RadixStore()
        s.insert(b"foo/bar", 1)
        s.insert(b"foo/baz", 2)
        s.delete(b"foo/baz")
        # after the merge the tree must be fully compressed again
        st = s.stats()
        self.assertEqual(st["edges"], 1)
        self.assertEqual(st["label_bytes"], len(b"foo/bar"))
        self.assertEqual(list(s.items()), [(b"foo/bar", 1)])

    def test_binary_keys(self):
        s = RadixStore()
        keys = [
            b"\x00",
            b"\x00\x00",
            b"\x00\xff\x00",
            b"\xff",
            b"\xff\xfe",
            bytes(range(256)),
            b"a\x00b",
            b"a\x00",
            b"\x80\x81\x82",
        ]
        for i, k in enumerate(keys):
            s.insert(k, i)
        self.assertEqual(len(s), len(keys))
        self.assertEqual([k for k, _ in s.items()], sorted(keys))
        for i, k in enumerate(keys):
            self.assertEqual(s[k], i)
        # prefix queries on binary prefixes
        self.assertEqual(
            [k for k, _ in s.items_with_prefix(b"\x00")],
            sorted(k for k in keys if k.startswith(b"\x00")),
        )
        self.assertEqual(
            [k for k, _ in s.items_with_prefix(b"\xff")],
            sorted(k for k in keys if k.startswith(b"\xff")),
        )

    def test_long_keys(self):
        s = RadixStore()
        shared = b"/very/long/shared/prefix/" * 400  # ~10 KB
        k1 = shared + b"\x00tail-a"
        k2 = shared + b"\x00tail-b"
        k3 = shared  # prefix of the other two
        for i, k in enumerate((k1, k2, k3)):
            s.insert(k, i)
        self.assertEqual(len(s), 3)
        self.assertEqual([k for k, _ in s.items()], sorted([k1, k2, k3]))
        self.assertEqual(s[k2], 1)
        self.assertEqual(len(list(s.items_with_prefix(shared))), 3)
        s.delete(k3)
        self.assertEqual([k for k, _ in s.items()], sorted([k1, k2]))

    def test_key_type_validation(self):
        s = RadixStore()
        with self.assertRaises(TypeError):
            s.insert("not-bytes", 1)
        s.insert(bytearray(b"ok"), 1)  # bytes-like accepted
        s.insert(memoryview(b"mv"), 2)
        self.assertIn(b"ok", s)
        self.assertIn(b"mv", s)

    def test_many_keys_sorted(self):
        rng = random.Random(1234)
        keys = set()
        while len(keys) < 20000:
            key = (b"tenant-%03d/" % rng.randrange(50)
                   + b"obj-%06d" % rng.randrange(200000))
            keys.add(key)
        s = RadixStore()
        for k in keys:
            s.insert(k, True)
        self.assertEqual(len(s), len(keys))
        self.assertEqual(list(s.keys()), sorted(keys))


class TestDifferential(unittest.TestCase):
    """Random-operation differential test vs. a naive dict model."""

    def _random_key(self, rng):
        # heavy prefix sharing + binary bytes (incl. \x00 and \xff)
        prefix = rng.choice([
            b"", b"users/", b"users/alice/", b"users/bob/",
            b"\x00\x01", b"\xff", b"users/",
            b"a" * rng.randrange(0, 30),
        ])
        suffix = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 12)))
        return prefix + suffix

    def test_against_naive_model(self):
        for seed in range(6):
            rng = random.Random(seed)
            s = RadixStore()
            model = {}
            live_keys = []
            for step in range(4000):
                op = rng.random()
                if op < 0.45 or not live_keys:
                    key = self._random_key(rng)
                    value = rng.randrange(1 << 30)
                    s.insert(key, value)
                    if key not in model:
                        live_keys.append(key)
                    model[key] = value
                elif op < 0.70:
                    key = rng.choice(live_keys)
                    if rng.random() < 0.5:
                        s.delete(key)
                        del model[key]
                        live_keys.remove(key)
                    else:
                        self.assertIn(key, s)
                elif op < 0.85:
                    key = self._random_key(rng)
                    self.assertEqual(s.get(key), model.get(key), (seed, step, key))
                    self.assertEqual(key in s, key in model)
                elif op < 0.95:
                    prefix = self._random_key(rng)[:rng.randrange(0, 8)]
                    got = list(s.items_with_prefix(prefix))
                    want = model_prefix(model, prefix)
                    self.assertEqual(got, want, (seed, step, prefix))
                else:
                    self.assertEqual(list(s.items()), model_items(model),
                                     (seed, step))
                self.assertEqual(len(s), len(model), (seed, step))
            # final full-traversal comparison
            self.assertEqual(list(s.items()), model_items(model))
            # packed (immutable array-based) form must answer identically
            packed = PackedRadixStore.from_store(s)
            self.assertEqual(len(packed), len(model))
            self.assertEqual(list(packed.items()), model_items(model))
            for _ in range(200):
                key = self._random_key(rng)
                self.assertEqual(packed.get(key), model.get(key))
                prefix = key[:rng.randrange(0, 8)]
                self.assertEqual(list(packed.items_with_prefix(prefix)),
                                 model_prefix(model, prefix))
            # delete everything; store must end up fully compressed (empty)
            for key in list(model):
                s.delete(key)
            self.assertEqual(len(s), 0)
            self.assertEqual(list(s.items()), [])
            self.assertEqual(s.stats()["edges"], 0)

    def test_packed_from_items(self):
        rng = random.Random(42)
        keys = [b"p/%06d" % rng.randrange(5000) for _ in range(3000)]
        items = sorted({k: i for i, k in enumerate(keys)}.items())
        packed = PackedRadixStore.from_items(items)
        self.assertEqual(len(packed), len(items))
        self.assertEqual(list(packed.items()), items)
        self.assertEqual(list(packed.keys_with_prefix(b"p/0012")),
                         [k for k, _ in items if k.startswith(b"p/0012")])
        self.assertNotIn(b"nope", packed)


if __name__ == "__main__":
    unittest.main()
