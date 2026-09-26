"""test_merkle.py — 哈希树库自测。运行: python3 test_merkle.py -v"""

import os
import unittest

from merkle import (
    EMPTY_ROOT,
    HASH_SIZE,
    MerkleTree,
    ProofFormatError,
    leaf_hash,
    node_hash,
    verify,
    verify_explained,
)


def make_blocks(n, size=32):
    return [os.urandom(size) for _ in range(n)]


class TestBuildAndRoot(unittest.TestCase):
    def test_empty_set_root_is_constant(self):
        t = MerkleTree([])
        self.assertEqual(t.size, 0)
        self.assertEqual(t.height, 0)
        self.assertEqual(t.root, EMPTY_ROOT)
        self.assertEqual(t.root, MerkleTree().root)

    def test_single_block_root_is_leaf_hash(self):
        blocks = make_blocks(1)
        t = MerkleTree(blocks)
        self.assertEqual(t.height, 0)
        self.assertEqual(t.root, leaf_hash(blocks[0]))
        self.assertEqual(t.prove(0), [])

    def test_two_blocks(self):
        blocks = make_blocks(2)
        t = MerkleTree(blocks)
        self.assertEqual(t.height, 1)
        expected = node_hash(leaf_hash(blocks[0]), leaf_hash(blocks[1]))
        self.assertEqual(t.root, expected)

    def test_odd_count_duplicates_last(self):
        blocks = make_blocks(3)
        t = MerkleTree(blocks)
        h0, h1, h2 = (leaf_hash(b) for b in blocks)
        expected = node_hash(node_hash(h0, h1), node_hash(h2, h2))  # 复制末尾
        self.assertEqual(t.root, expected)
        self.assertEqual(t.height, 2)

    def test_empty_data_block(self):
        blocks = [b"", b"", os.urandom(8), b""]
        t = MerkleTree(blocks)
        for i, b in enumerate(blocks):
            ok, _ = verify_explained(t.root, b, t.prove(i))
            self.assertTrue(ok, f"空数据块下标 {i} 验证失败")
        # 空数据块与空集合的根必须不同
        self.assertNotEqual(t.root, EMPTY_ROOT)

    def test_large_set(self):
        blocks = make_blocks(10_001)  # 上万块，且为奇数
        t = MerkleTree(blocks)
        self.assertEqual(t.size, 10_001)
        self.assertEqual(t.height, 14)  # ceil(log2(10001)) = 14
        for i in (0, 1, 5000, 10_000):
            ok, _ = verify_explained(t.root, blocks[i], t.prove(i))
            self.assertTrue(ok)

    def test_deterministic(self):
        blocks = make_blocks(100)
        self.assertEqual(MerkleTree(blocks).root, MerkleTree(blocks).root)


class TestProofVerification(unittest.TestCase):
    def setUp(self):
        self.blocks = make_blocks(7)  # 奇数个，覆盖复制末尾分支
        self.tree = MerkleTree(self.blocks)

    def test_all_leaves_verify(self):
        for i, b in enumerate(self.blocks):
            self.assertTrue(verify(self.tree.root, b, self.tree.prove(i)))

    def test_proof_length_equals_height(self):
        for i in range(len(self.blocks)):
            self.assertEqual(len(self.tree.prove(i)), self.tree.height)

    def test_tampered_block_rejected(self):
        proof = self.tree.prove(3)
        forged = bytearray(self.blocks[3])
        forged[0] ^= 0xFF
        ok, reason = verify_explained(self.tree.root, bytes(forged), proof)
        self.assertFalse(ok)
        self.assertIn("不一致", reason)

    def test_swapped_siblings_rejected(self):
        proof = self.tree.prove(2)
        swapped = [("R" if d == "L" else "L", h) for d, h in proof]
        ok, reason = verify_explained(self.tree.root, self.blocks[2], swapped)
        self.assertFalse(ok)
        self.assertIn("不一致", reason)

    def test_truncated_proof_rejected(self):
        proof = self.tree.prove(5)
        ok, reason = verify_explained(self.tree.root, self.blocks[5], proof[:-1])
        self.assertFalse(ok)
        self.assertIn("不一致", reason)

    def test_forged_root_rejected(self):
        proof = self.tree.prove(0)
        fake_root = bytearray(self.tree.root)
        fake_root[-1] ^= 0x01
        ok, reason = verify_explained(self.tree.root.__class__(fake_root),
                                      self.blocks[0], proof)
        self.assertFalse(ok)
        self.assertIn("不一致", reason)

    def test_wrong_leaf_rejected(self):
        # 用下标 1 的数据 + 下标 2 的证明
        ok, _ = verify_explained(self.tree.root, self.blocks[1], self.tree.prove(2))
        self.assertFalse(ok)

    def test_malformed_proof_rejected(self):
        with self.assertRaises(ProofFormatError):
            verify(self.tree.root, self.blocks[0], [("X", b"\x00" * 32)])
        with self.assertRaises(ProofFormatError):
            verify(self.tree.root, self.blocks[0], [("L", b"short")])
        with self.assertRaises(ProofFormatError):
            verify(self.tree.root, self.blocks[0], "not-a-proof")
        with self.assertRaises(ProofFormatError):
            verify(b"bad-root", self.blocks[0], [])
        ok, reason = verify_explained(self.tree.root, self.blocks[0],
                                      [("X", b"\x00" * 32)])
        self.assertFalse(ok)
        self.assertIn("方向非法", reason)

    def test_cross_tree_proof_rejected(self):
        other = MerkleTree(make_blocks(7))
        ok, _ = verify_explained(self.tree.root, self.blocks[0], other.prove(0))
        self.assertFalse(ok)


class TestIncrementalUpdate(unittest.TestCase):
    def test_update_matches_rebuild(self):
        blocks = make_blocks(1_000)
        t = MerkleTree(blocks)
        for idx in (0, 1, 499, 999):
            new_block = os.urandom(32)
            t.update(idx, new_block)
            blocks[idx] = new_block
            self.assertEqual(t.root, MerkleTree(blocks).root,
                             f"增量更新下标 {idx} 后与整树重建根不一致")

    def test_append_matches_rebuild(self):
        blocks = make_blocks(1)
        t = MerkleTree(blocks)
        for _ in range(600):  # 跨越多次根分裂与奇偶交替
            new_block = os.urandom(16)
            t.append(new_block)
            blocks.append(new_block)
            self.assertEqual(t.root, MerkleTree(blocks).root,
                             f"追加到 {len(blocks)} 块后与整树重建根不一致")

    def test_update_then_proofs_still_valid(self):
        blocks = make_blocks(513)
        t = MerkleTree(blocks)
        blocks[100] = os.urandom(32)
        t.update(100, blocks[100])
        for i in (0, 100, 512):
            self.assertTrue(verify(t.root, blocks[i], t.prove(i)))

    def test_bounds_checking(self):
        t = MerkleTree(make_blocks(4))
        with self.assertRaises(IndexError):
            t.update(4, b"x")
        with self.assertRaises(IndexError):
            t.prove(-1)


class TestProofLengthVsHeight(unittest.TestCase):
    def test_relation(self):
        import math
        for n in (1, 2, 3, 4, 5, 7, 8, 9, 1000, 10_000):
            t = MerkleTree(make_blocks(n))
            expected = 0 if n == 1 else math.ceil(math.log2(n))
            self.assertEqual(t.height, expected)
            self.assertEqual(len(t.prove(n - 1)), expected)


if __name__ == "__main__":
    unittest.main()
