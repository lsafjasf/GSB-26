"""Self-tests for the fuzzgen framework. Run: python3 test_fuzzgen.py"""

import json
import random
import unittest

import fuzzgen as fg
import sut
from compare import gen_structured
from schema import BATCH, batch_shape


def serialize(values):
    return [json.dumps(v, sort_keys=True) for v in values]


def meta_depth(m):
    return 1 + (meta_depth(m["sub"]) if m["sub"] is not None else 0)


def max_depth_of(batch):
    return max(meta_depth(it["meta"]) for it in batch["items"])


class ReproducibilityTest(unittest.TestCase):
    def test_same_seed_same_sequence(self):
        a = serialize(gen_structured(seed=42, n=300))
        b = serialize(gen_structured(seed=42, n=300))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 300)

    def test_same_seed_prefix_stable(self):
        full = serialize(gen_structured(seed=7, n=300))
        prefix = serialize(gen_structured(seed=7, n=100))
        self.assertEqual(full[:100], prefix)

    def test_different_seeds_differ(self):
        a = serialize(gen_structured(seed=1, n=300))
        b = serialize(gen_structured(seed=2, n=300))
        self.assertNotEqual(a, b)


class StructuralValidityTest(unittest.TestCase):
    def test_all_modes_structurally_legal(self):
        for seed in (0, 1, 12345):
            for value in gen_structured(seed=seed, n=300):
                # parses as JSON and matches the schema shape exactly,
                # in both valid and violate modes
                self.assertTrue(batch_shape(value))
                json.loads(json.dumps(value))  # round-trips as JSON

    def test_valid_mode_passes_business_rules(self):
        rng = random.Random(99)
        for _ in range(300):
            value = BATCH.gen(fg.Ctx(rng, max_depth=3, violate=False))
            self.assertEqual(sut.run(json.dumps(value).encode()), "OK")

    def test_violate_mode_breaks_business_not_structure(self):
        rng = random.Random(100)
        rejected = 0
        total = 400
        for _ in range(total):
            value = BATCH.gen(fg.Ctx(rng, max_depth=8, violate=True))
            self.assertTrue(batch_shape(value))  # structure still legal
            try:
                sut.run(json.dumps(value).encode())
            except sut.Reject:
                rejected += 1
        # violation mode must actually trigger validation branches
        self.assertGreater(rejected / total, 0.5)


class RecursionTest(unittest.TestCase):
    def test_depth_hard_cap(self):
        for max_depth in (0, 1, 3, 8):
            rng = random.Random(max_depth)
            for _ in range(200):
                value = BATCH.gen(fg.Ctx(rng, max_depth=max_depth,
                                         violate=True))
                # nesting = at most max_depth recursions + 1 base level
                self.assertLessEqual(max_depth_of(value), max_depth + 1)

    def test_no_infinite_recursion_even_greedy(self):
        greedy = fg.Recursive(
            base=fg.Const("leaf"),
            extend=lambda rec: fg.Record({"sub": rec}),
            recurse_weight=0.999,
        )
        rng = random.Random(5)
        for _ in range(2000):  # completes => terminates
            value = greedy.gen(fg.Ctx(rng, max_depth=10))
            depth = 0
            while isinstance(value, dict):
                depth += 1
                value = value["sub"]
            self.assertLessEqual(depth, 11)


class BoundaryBiasTest(unittest.TestCase):
    def test_boundary_values_reached(self):
        rng = random.Random(11)
        gen = fg.Int(1, 500, boundary_weight=0.5)
        seen = {gen.gen(fg.Ctx(rng)) for _ in range(500)}
        self.assertIn(1, seen)
        self.assertIn(500, seen)

    def test_oversized_and_empty_in_violate_mode(self):
        rng = random.Random(13)
        gen = fg.Text(min_len=1, max_len=16)
        lens = {len(gen.gen(fg.Ctx(rng, violate=True))) for _ in range(500)}
        self.assertIn(0, lens)                 # empty
        self.assertTrue(any(n > 16 for n in lens))  # oversized


if __name__ == "__main__":
    unittest.main(verbosity=2)
