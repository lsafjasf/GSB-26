"""Proof-verification and incremental-update tests for merkle.py."""

import os
import random
import unittest

from merkle import (
    EMPTY_ROOT,
    LEFT,
    RIGHT,
    MerkleTree,
    Proof,
    hash_leaf,
    tree_height,
    verify,
    verify_detailed,
)


def make_blocks(n, size=32, seed=42):
    rng = random.Random(seed)
    return [rng.randbytes(size) for _ in range(n)]


class TestConstruction(unittest.TestCase):
    def test_empty_set_root_is_defined_constant(self):
        tree = MerkleTree([])
        self.assertEqual(tree.root, EMPTY_ROOT)
        self.assertEqual(tree.leaf_count, 0)
        self.assertEqual(tree.height, 0)
        with self.assertRaises(IndexError):
            tree.prove(0)

    def test_single_block_root_is_leaf_hash_and_empty_proof(self):
        data = b"hello"
        tree = MerkleTree([data])
        self.assertEqual(tree.root, hash_leaf(data).hex())
        proof = tree.prove(0)
        self.assertEqual(proof.siblings, [])
        ok, reason = verify_detailed(tree.root, proof, data)
        self.assertTrue(ok, reason)

    def test_two_blocks(self):
        blocks = make_blocks(2)
        tree = MerkleTree(blocks)
        self.assertEqual(tree.height, 1)
        for i, block in enumerate(blocks):
            ok, reason = verify_detailed(tree.root, tree.prove(i), block)
            self.assertTrue(ok, reason)

    def test_odd_counts_promote(self):
        for n in (3, 5, 7, 9, 15, 17, 31, 33):
            blocks = make_blocks(n, seed=n)
            tree = MerkleTree(blocks)
            for i, block in enumerate(blocks):
                self.assertTrue(
                    verify(tree.root, tree.prove(i), block),
                    f"n={n} index={i}",
                )

    def test_empty_data_block_is_valid_leaf(self):
        blocks = [b"", b"x", b""]
        tree = MerkleTree(blocks)
        for i, block in enumerate(blocks):
            ok, reason = verify_detailed(tree.root, tree.prove(i), block)
            self.assertTrue(ok, reason)
        # b"" leaf differs from empty-tree root
        self.assertNotEqual(tree.root, EMPTY_ROOT)

    def test_large_set(self):
        blocks = make_blocks(20_000, size=64)
        tree = MerkleTree(blocks)
        self.assertEqual(tree.height, tree_height(20_000))
        rng = random.Random(0)
        for i in rng.sample(range(len(blocks)), 500):
            self.assertTrue(verify(tree.root, tree.prove(i), blocks[i]), f"index={i}")

    def test_proof_roundtrip_serialization(self):
        blocks = make_blocks(11)
        tree = MerkleTree(blocks)
        proof = Proof.from_dict(tree.prove(4).to_dict())
        self.assertTrue(verify(tree.root, proof, blocks[4]))


class TestStrictVerification(unittest.TestCase):
    def setUp(self):
        self.blocks = make_blocks(8)
        self.tree = MerkleTree(self.blocks)

    def test_tampered_data_rejected(self):
        proof = self.tree.prove(3)
        ok, reason = verify_detailed(self.tree.root, proof, b"forged data")
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

    def test_swapped_sibling_order_rejected(self):
        # index 0 proof: all siblings on the RIGHT; reversing the list
        # changes the recomputed root and must be rejected.
        proof = self.tree.prove(0)
        swapped = Proof(proof.leaf_index, proof.leaf_count, list(reversed(proof.siblings)))
        ok, reason = verify_detailed(self.tree.root, swapped, self.blocks[0])
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

    def test_flipped_direction_rejected(self):
        proof = self.tree.prove(1)  # first sibling is on the LEFT
        tampered = Proof(
            proof.leaf_index,
            proof.leaf_count,
            [(RIGHT if d == LEFT else LEFT, h) for d, h in proof.siblings],
        )
        ok, reason = verify_detailed(self.tree.root, tampered, self.blocks[1])
        self.assertFalse(ok)

    def test_truncated_path_rejected_with_length_reason(self):
        proof = self.tree.prove(5)
        truncated = Proof(proof.leaf_index, proof.leaf_count, proof.siblings[:-1])
        ok, reason = verify_detailed(self.tree.root, truncated, self.blocks[5])
        self.assertFalse(ok)
        self.assertIn("truncated or padded", reason)

    def test_padded_path_rejected(self):
        proof = self.tree.prove(5)
        padded = Proof(
            proof.leaf_index, proof.leaf_count, proof.siblings + [(LEFT, "00" * 32)]
        )
        ok, reason = verify_detailed(self.tree.root, padded, self.blocks[5])
        self.assertFalse(ok)
        self.assertIn("truncated or padded", reason)

    def test_forged_root_rejected(self):
        proof = self.tree.prove(2)
        forged_root = os.urandom(32).hex()
        ok, reason = verify_detailed(forged_root, proof, self.blocks[2])
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

    def test_wrong_index_rejected(self):
        proof = self.tree.prove(2)
        shifted = Proof(3, proof.leaf_count, proof.siblings)
        ok, _ = verify_detailed(self.tree.root, shifted, self.blocks[2])
        self.assertFalse(ok)

    def test_out_of_range_index_rejected(self):
        proof = Proof(8, 8, [])
        ok, reason = verify_detailed(self.tree.root, proof, b"x")
        self.assertFalse(ok)
        self.assertIn("out of range", reason)

    def test_malformed_root_rejected(self):
        proof = self.tree.prove(0)
        for bad_root in ("zz", "abc", "", "00" * 31):
            ok, reason = verify_detailed(bad_root, proof, self.blocks[0])
            self.assertFalse(ok, bad_root)
            self.assertIn("root digest", reason)

    def test_malformed_sibling_rejected(self):
        proof = self.tree.prove(0)
        bad = Proof(proof.leaf_index, proof.leaf_count,
                    [(RIGHT, "nothex")] + proof.siblings[1:])
        ok, reason = verify_detailed(self.tree.root, bad, self.blocks[0])
        self.assertFalse(ok)
        self.assertIn("non-hex", reason)


class TestIncrementalUpdate(unittest.TestCase):
    def test_update_matches_full_rebuild(self):
        blocks = make_blocks(1000)
        tree = MerkleTree(blocks)
        rng = random.Random(7)
        for _ in range(50):
            i = rng.randrange(len(blocks))
            blocks[i] = os.urandom(32)
            tree.update(i, blocks[i])
            self.assertEqual(tree.root, MerkleTree(blocks).root, f"index={i}")

    def test_append_matches_full_rebuild(self):
        blocks = make_blocks(3)
        tree = MerkleTree(blocks)
        for n in range(3, 600):
            block = os.urandom(16)
            blocks.append(block)
            tree.append(block)
            self.assertEqual(tree.root, MerkleTree(blocks).root, f"n={n + 1}")
            self.assertEqual(tree.leaf_count, n + 1)

    def test_append_then_prove_and_verify(self):
        blocks = make_blocks(10)
        tree = MerkleTree(blocks)
        extra = os.urandom(32)
        blocks.append(extra)
        tree.append(extra)
        self.assertTrue(verify(tree.root, tree.prove(10), extra))
        for i in range(10):
            self.assertTrue(verify(tree.root, tree.prove(i), blocks[i]))

    def test_update_out_of_range(self):
        tree = MerkleTree(make_blocks(4))
        with self.assertRaises(IndexError):
            tree.update(4, b"x")


class TestProofShape(unittest.TestCase):
    def test_proof_length_bounded_by_height(self):
        for n in (1, 2, 3, 4, 5, 8, 9, 100, 1023, 1024, 1025):
            tree = MerkleTree(make_blocks(n, seed=n))
            for i in range(n):
                proof = tree.prove(i)
                self.assertLessEqual(len(proof.siblings), tree.height, f"n={n} i={i}")

    def test_promoted_leaf_has_shorter_proof(self):
        # n=3: leaf 2 is promoted at level 0 -> proof length 1, height 2
        tree = MerkleTree(make_blocks(3))
        self.assertEqual(tree.height, 2)
        self.assertEqual(len(tree.prove(2).siblings), 1)
        self.assertEqual(len(tree.prove(0).siblings), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
