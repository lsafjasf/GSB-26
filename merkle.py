"""Merkle hash tree for set-integrity verification (stdlib only).

Rules (shared by build and verify):
  - Leaf:        H(0x00 || data)
  - Inner node:  H(0x01 || left || right)
  - Odd node:    promoted (carried up unchanged), NOT duplicated
  - Empty set:   root = H(b"")  (a well-defined constant)
  - Single leaf: root = that leaf's hash, proof is empty
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

LEAF_TAG = b"\x00"
NODE_TAG = b"\x01"

LEFT = "L"   # sibling is on the left of the current node
RIGHT = "R"  # sibling is on the right


def hash_leaf(data: bytes) -> bytes:
    return hashlib.sha256(LEAF_TAG + data).digest()


def hash_node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_TAG + left + right).digest()


EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def tree_height(leaf_count: int) -> int:
    """Number of edges on the longest root-to-leaf path."""
    if leaf_count <= 1:
        return 0
    height = 0
    level = leaf_count
    while level > 1:
        level = (level + 1) // 2
        height += 1
    return height


@dataclass
class Proof:
    """Inclusion proof for one data block.

    siblings[i] = (direction, hex_digest) walked leaf -> root.
    A promoted (odd) node contributes no entry, so len(siblings) <= height.
    """
    leaf_index: int
    leaf_count: int
    siblings: List[Tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "leaf_index": self.leaf_index,
            "leaf_count": self.leaf_count,
            "siblings": [[d, h] for d, h in self.siblings],
        }

    @staticmethod
    def from_dict(obj: dict) -> "Proof":
        return Proof(
            leaf_index=obj["leaf_index"],
            leaf_count=obj["leaf_count"],
            siblings=[(d, h) for d, h in obj["siblings"]],
        )


class MerkleTree:
    """Append/update-friendly Merkle tree over a list of data blocks."""

    def __init__(self, blocks: Sequence[bytes] = ()):
        self._leaves: List[bytes] = []
        self._levels: List[List[bytes]] = []  # levels[0] = leaf hashes
        for block in blocks:
            if not isinstance(block, (bytes, bytearray)):
                raise TypeError("blocks must be bytes")
            self._leaves.append(hash_leaf(bytes(block)))
        if self._leaves:
            self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        level = list(self._leaves)
        self._levels = [level]
        while len(level) > 1:
            parent = []
            for i in range(0, len(level) - 1, 2):
                parent.append(hash_node(level[i], level[i + 1]))
            if len(level) % 2 == 1:
                parent.append(level[-1])  # odd node promoted unchanged
            self._levels.append(parent)
            level = parent

    # ------------------------------------------------------------- properties
    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    @property
    def height(self) -> int:
        return tree_height(len(self._leaves))

    @property
    def root(self) -> str:
        """Root digest as hex; H(b\"\") for the empty set."""
        if not self._levels:
            return EMPTY_ROOT
        return self._levels[-1][0].hex()

    # ------------------------------------------------------------------ proof
    def prove(self, index: int) -> Proof:
        if not 0 <= index < len(self._leaves):
            raise IndexError(f"index {index} out of range for {len(self._leaves)} blocks")
        siblings: List[Tuple[str, str]] = []
        i = index
        for level in self._levels[:-1]:
            if i % 2 == 0:
                if i + 1 < len(level):
                    siblings.append((RIGHT, level[i + 1].hex()))
                # else: odd node promoted, no sibling at this level
            else:
                siblings.append((LEFT, level[i - 1].hex()))
            i //= 2
        return Proof(leaf_index=index, leaf_count=len(self._leaves), siblings=siblings)

    # ------------------------------------------------------------- mutation
    def update(self, index: int, data: bytes) -> None:
        """Replace block `index`; only the affected path is recomputed."""
        if not 0 <= index < len(self._leaves):
            raise IndexError(f"index {index} out of range for {len(self._leaves)} blocks")
        self._leaves[index] = hash_leaf(data)
        self._levels[0][index] = self._leaves[index]
        i = index
        for lvl in range(len(self._levels) - 1):
            level = self._levels[lvl]
            parent = i // 2
            left = level[2 * parent]
            if 2 * parent + 1 < len(level):
                self._levels[lvl + 1][parent] = hash_node(left, level[2 * parent + 1])
            else:
                self._levels[lvl + 1][parent] = left  # promoted
            i = parent

    def append(self, data: bytes) -> None:
        """Append one block; only the affected path is recomputed."""
        self._leaves.append(hash_leaf(data))
        if not self._levels:
            self._levels = [[self._leaves[0]]]
            return
        self._levels[0].append(self._leaves[-1])
        lvl = 0
        while True:
            level = self._levels[lvl]
            if len(level) % 2 == 0:
                value = hash_node(level[-2], level[-1])
            else:
                value = level[-1]  # promoted
            pidx = (len(level) - 1) // 2
            if lvl + 1 == len(self._levels):
                self._levels.append([value])
                return
            parent_level = self._levels[lvl + 1]
            if pidx < len(parent_level):
                parent_level[pidx] = value
            else:
                parent_level.append(value)
            if len(parent_level) == 1:
                return  # root updated
            lvl += 1


# --------------------------------------------------------------- verification
def _expected_directions(leaf_count: int, leaf_index: int) -> List[str]:
    """Exact sibling directions for `leaf_index` under the promotion rule."""
    directions: List[str] = []
    level = leaf_count
    i = leaf_index
    while level > 1:
        if i % 2 == 0 and i + 1 == level:
            pass  # promoted odd node: no sibling
        else:
            directions.append(LEFT if i % 2 == 1 else RIGHT)
        i //= 2
        level = (level + 1) // 2
    return directions


def verify_detailed(root_hex: str, proof: Proof, data: bytes) -> Tuple[bool, str]:
    """Verify inclusion of `data` under `root_hex`. Returns (ok, reason)."""
    if not isinstance(root_hex, str) or len(root_hex) != 64:
        return False, "malformed root digest (expected 64 hex chars)"
    try:
        int(root_hex, 16)
    except ValueError:
        return False, "malformed root digest (not hex)"

    if proof.leaf_count < 1:
        return False, "proof rejected: empty tree has no inclusion proofs"
    if not 0 <= proof.leaf_index < proof.leaf_count:
        return False, (
            f"proof rejected: leaf_index {proof.leaf_index} out of range "
            f"for leaf_count {proof.leaf_count}"
        )

    expected_directions = _expected_directions(proof.leaf_count, proof.leaf_index)
    if len(proof.siblings) != len(expected_directions):
        return False, (
            f"proof rejected: path length {len(proof.siblings)} does not match "
            f"expected {len(expected_directions)} for index {proof.leaf_index} in a tree of "
            f"{proof.leaf_count} leaves (truncated or padded path)"
        )

    node = hash_leaf(data)
    for step, (direction, sib_hex) in enumerate(proof.siblings):
        if direction not in (LEFT, RIGHT):
            return False, f"proof rejected: bad direction {direction!r} at step {step}"
        if direction != expected_directions[step]:
            return False, (
                f"proof rejected: sibling direction at step {step} inconsistent "
                f"with leaf_index {proof.leaf_index}"
            )
        try:
            sib = bytes.fromhex(sib_hex)
        except ValueError:
            return False, f"proof rejected: non-hex sibling hash at step {step}"
        if len(sib) != 32:
            return False, f"proof rejected: sibling hash at step {step} is not 32 bytes"
        if direction == LEFT:
            node = hash_node(sib, node)
        else:
            node = hash_node(node, sib)

    if node.hex() != root_hex.lower():
        return False, (
            "proof rejected: computed root does not match the given root digest "
            "(tampered data, swapped siblings, or forged root)"
        )
    return True, "ok"


def verify(root_hex: str, proof: Proof, data: bytes) -> bool:
    """Strict boolean wrapper around verify_detailed."""
    return verify_detailed(root_hex, proof, data)[0]
