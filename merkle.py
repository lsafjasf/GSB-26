"""
merkle.py — 哈希树（Merkle Tree）库，用于集合完整性校验。仅依赖标准库。

规则约定（构建与验证严格一致）：
  1. 叶子哈希:  H_leaf(d)  = SHA256(b"\\x00" || d)        —— 域分离前缀 0x00
  2. 内部节点:  H_node(l,r) = SHA256(b"\\x01" || l || r)   —— 域分离前缀 0x01
  3. 奇数节点:  某一层的最后一个节点若没有右兄弟，则与自身配对（复制末尾，
     即 Bitcoin 风格 duplicate-last）。该规则同时体现在证明中：落单的节点
     其证明元素为 ('R', 自身哈希)，验证方按同一规则重算。
  4. 空集合:    根摘要为常量 EMPTY_ROOT = SHA256(b"MHT/empty")，树高为 0，
     不存在任何包含证明。
  5. 单叶子树:  根即叶子哈希本身（不再自配对），树高为 0，证明为空列表。

证明格式:  [(direction, sibling_hash), ...]，自叶子向根排列；
  direction = 'L' 表示兄弟在左（当前节点为右子），'R' 表示兄弟在右。
  方向编码进证明后，验证无需额外传入叶子下标；任何对数据、顺序、
  路径长度、根摘要的篡改都会导致重算结果与根摘要不一致而被拒绝。
"""

from __future__ import annotations

import hashlib
import hmac
from typing import List, Sequence, Tuple

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
HASH_SIZE = 32  # SHA-256 输出长度

EMPTY_ROOT = hashlib.sha256(b"MHT/empty").digest()

# 证明元素: (方向 'L'/'R', 兄弟哈希)
ProofElement = Tuple[str, bytes]
Proof = List[ProofElement]


def leaf_hash(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("数据块必须是 bytes 类型")
    return hashlib.sha256(LEAF_PREFIX + bytes(data)).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _build_levels(leaves: List[bytes]) -> List[List[bytes]]:
    """自底向上构建所有层；levels[0] 为叶子层，levels[-1] 为根层。"""
    levels = [list(leaves)]
    current = levels[0]
    while len(current) > 1:
        nxt = []
        for j in range(0, len(current), 2):
            left = current[j]
            right = current[j + 1] if j + 1 < len(current) else left  # 奇数: 复制末尾
            nxt.append(node_hash(left, right))
        levels.append(nxt)
        current = nxt
    return levels


class MerkleTree:
    """支持构建、证明生成与增量更新（修改/追加）的哈希树。"""

    def __init__(self, blocks: Sequence[bytes] = ()):
        self._levels: List[List[bytes]] = _build_levels(
            [leaf_hash(b) for b in blocks]
        )

    # ---------- 基本属性 ----------

    @property
    def size(self) -> int:
        return len(self._levels[0])

    @property
    def height(self) -> int:
        """树高 = 根到叶子的边数 = 证明长度。空树与单叶子树高度为 0。"""
        return len(self._levels) - 1 if self.size > 0 else 0

    @property
    def root(self) -> bytes:
        if self.size == 0:
            return EMPTY_ROOT
        return self._levels[-1][0]

    # ---------- 包含证明 ----------

    def prove(self, index: int) -> Proof:
        if not 0 <= index < self.size:
            raise IndexError(f"下标越界: {index}（集合大小 {self.size}）")
        proof: Proof = []
        i = index
        for level in range(len(self._levels) - 1):
            cur = self._levels[level]
            sib = i ^ 1
            if sib < len(cur):
                proof.append(("L" if sib < i else "R", cur[sib]))
            else:
                # 奇数规则: 落单节点与自身配对
                proof.append(("R", cur[i]))
            i //= 2
        return proof

    # ---------- 增量更新 ----------

    def update(self, index: int, data: bytes) -> None:
        """修改一个数据块，只重算从该叶子到根的路径，O(log n)。"""
        if not 0 <= index < self.size:
            raise IndexError(f"下标越界: {index}（集合大小 {self.size}）")
        self._levels[0][index] = leaf_hash(data)
        i = index
        for level in range(len(self._levels) - 1):
            cur = self._levels[level]
            pi = i // 2
            left = cur[2 * pi]
            right = cur[2 * pi + 1] if 2 * pi + 1 < len(cur) else left
            self._levels[level + 1][pi] = node_hash(left, right)
            i = pi

    def append(self, data: bytes) -> None:
        """追加一个数据块，只重算最右侧受影响路径（含根分裂），O(log n)。"""
        self._levels[0].append(leaf_hash(data))
        i = self.size - 1
        level = 0
        while True:
            cur = self._levels[level]
            if len(cur) == 1 and level == len(self._levels) - 1:
                break  # 单节点即根（空树首次插入）
            if level == len(self._levels) - 1:
                self._levels.append([])  # 根分裂，长出新的一层
            parent_level = self._levels[level + 1]
            pi = i // 2
            left = cur[2 * pi]
            right = cur[2 * pi + 1] if 2 * pi + 1 < len(cur) else left
            ph = node_hash(left, right)
            if pi < len(parent_level):
                parent_level[pi] = ph
            else:
                parent_level.append(ph)
            if len(parent_level) == 1 and level + 1 == len(self._levels) - 1:
                break  # 已到达根
            i = pi
            level += 1

    # ---------- 便捷方法 ----------

    def root_hex(self) -> str:
        return self.root.hex()


class ProofFormatError(ValueError):
    """证明格式非法（类型、方向、哈希长度等）。"""


def _check_proof_format(proof: Sequence[ProofElement]) -> None:
    if not isinstance(proof, (list, tuple)):
        raise ProofFormatError("证明必须是 (方向, 哈希) 组成的列表")
    for k, item in enumerate(proof):
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ProofFormatError(f"证明第 {k} 个元素不是 (方向, 哈希) 对")
        direction, sib = item
        if direction not in ("L", "R"):
            raise ProofFormatError(f"证明第 {k} 个元素方向非法: {direction!r}")
        if not (isinstance(sib, bytes) and len(sib) == HASH_SIZE):
            raise ProofFormatError(f"证明第 {k} 个元素哈希不是 {HASH_SIZE} 字节")


def compute_root_from_proof(data: bytes, proof: Sequence[ProofElement]) -> bytes:
    """按与构建一致的规则，由数据块与证明重算根摘要。"""
    _check_proof_format(proof)
    h = leaf_hash(data)
    for direction, sib in proof:
        h = node_hash(sib, h) if direction == "L" else node_hash(h, sib)
    return h


def verify(root: bytes, data: bytes, proof: Sequence[ProofElement]) -> bool:
    """严格校验：格式非法抛 ProofFormatError；摘要不一致返回 False。"""
    if not (isinstance(root, bytes) and len(root) == HASH_SIZE):
        raise ProofFormatError(f"根摘要必须是 {HASH_SIZE} 字节")
    computed = compute_root_from_proof(data, proof)
    return hmac.compare_digest(computed, root)


def verify_explained(root: bytes, data: bytes, proof: Sequence[ProofElement]):
    """与 verify 相同，但返回 (是否通过, 原因说明)，便于诊断与测试。"""
    try:
        if not (isinstance(root, bytes) and len(root) == HASH_SIZE):
            return False, f"根摘要格式非法：应为 {HASH_SIZE} 字节"
        computed = compute_root_from_proof(data, proof)
    except ProofFormatError as exc:
        return False, f"证明格式非法：{exc}"
    except TypeError as exc:
        return False, f"数据块非法：{exc}"
    if hmac.compare_digest(computed, root):
        return True, "校验通过：由数据块与证明重算的根摘要与给定根一致"
    return False, (
        "校验失败：重算根摘要与给定根不一致"
        "（数据块被篡改、兄弟顺序被交换、路径被截断或根摘要被伪造均会导致此结果）"
    )
