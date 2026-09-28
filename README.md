# Merkle 哈希树库（Python 3，仅标准库）

用于集合完整性校验的哈希树（Merkle Tree）：从数据块集合构建树、计算根摘要、
为任一数据块生成包含证明；验证方只需根摘要 + 数据块 + 证明路径即可校验。
支持 O(log n) 增量更新（修改/追加）。

## 文件

| 文件 | 说明 |
|---|---|
| `merkle.py` | 库源码（构建、根摘要、证明生成/验证、增量更新） |
| `test_merkle.py` | 自测：28 个用例，含严格拒绝场景、块数绑定与证明不可复用断言 |
| `benchmark.py` | 增量 vs 整树重建耗时对比 + 证明长度与树高关系 |

## 运行命令

```bash
python3 test_merkle.py -v   # 运行自测
python3 benchmark.py        # 运行性能对比
```

## 规则约定（构建与验证严格一致）

1. **哈希算法**：SHA-256，带域分离前缀。
   - 叶子：`H_leaf(d) = SHA256(0x00 || d)`
   - 内部节点：`H_node(l, r) = SHA256(0x01 || l || r)`
2. **奇数节点**：某层最后一个节点无右兄弟时**原样提升**到上一层
   （promotion，不复制、不自配对、不另做哈希）。落单节点的证明元素为
   `('P', b'')`，验证方遇到 `'P'` 时保持当前哈希不变。这样根摘要随块数
   不同而不同，同一份证明无法在不同块数的集合间复用。
3. **空集合**：根摘要为常量 `EMPTY_ROOT = SHA256(b"MHT/empty")`，树高 0，
   不存在包含证明。空集合根与"空数据块"（`b""`）的叶子哈希不同，互不混淆。
4. **单数据块**：根即叶子哈希本身，树高 0，证明为空列表。
5. **证明格式**：`[(direction, sibling_hash), ...]`，自叶子向根排列；
   `direction='L'` 表示兄弟在左，`'R'` 表示兄弟在右，`'P'` 表示原样提升
   （此时 `sibling_hash` 固定为 `b''`）。方向编码进证明，验证无需传下标。

## 用法示例

```python
from merkle import MerkleTree, verify, verify_explained

tree = MerkleTree([b"block-0", b"block-1", b"block-2"])
root = tree.root                      # 根摘要（32 字节），对外发布
proof = tree.prove(1)                 # 为 block-1 生成包含证明

assert verify(root, b"block-1", proof)          # 验证方只需 root + 数据 + 证明

tree.update(0, b"block-0-new")        # 增量修改：只重算到根的路径
tree.append(b"block-3")               # 增量追加：只重算最右侧路径（含根分裂）

ok, reason = verify_explained(root, b"block-1", proof)  # 带原因说明的验证
```

## 严格验证（全部被拒绝并说明原因）

以下篡改均导致重算根与给定根不一致（或格式非法），测试见 `test_merkle.py`：

- **篡改数据块**：`test_tampered_block_rejected`
- **交换兄弟顺序**（翻转 L/R 方向）：`test_swapped_siblings_rejected`
- **截断路径**：`test_truncated_proof_rejected`
- **伪造根摘要**：`test_forged_root_rejected`
- 另覆盖：跨树证明、错误下标的证明、非法方向/哈希长度/根格式等。

验证使用 `hmac.compare_digest` 做常量时间比较；格式非法抛
`ProofFormatError`，`verify_explained` 返回 `(False, 原因)`。

## 性能数据（实测：100,000 块 × 64 字节，300 次操作）

| 操作 | 增量更新 | 整树重建 | 加速比 |
|---|---|---|---|
| 修改 1 块 | ~7.1 µs/次 | ~68,158 µs/次 | ~9,600× |
| 追加 1 块 | ~5.9 µs/次 | ~77,357 µs/次 | ~13,200× |

增量更新复杂度 O(log n)（每步 1 次哈希），整树重建 O(n)。

## 证明长度与树高关系

证明长度 == 树高 == `ceil(log2 n)`（n ≥ 2；n = 1 时为 0）：

| 块数 n | 树高/证明长度 | 证明大小（1 字节方向 + 32 字节哈希）/元素 |
|---|---|---|
| 1 | 0 | 0 B |
| 2 | 1 | 34 B |
| 3–4 | 2 | 68 B |
| 5–8 | 3 | 102 B |
| 1,000 | 10 | 340 B |
| 10,000 | 14 | 476 B |
| 100,000 | 17 | 578 B |

## 边界说明

- **空集合**：根为固定常量 `EMPTY_ROOT`；`prove`/`update` 对空树抛 `IndexError`。
- **空数据块**（`b""`）：是合法叶子，其哈希与空集合根、非空块哈希均不同。
- **奇数节点**：原样提升规则同时作用于构建、证明生成与验证三方；追加导致
  奇偶变化时，受影响父节点（含旧的提升节点）都在重算路径上。
  旧版复制末尾（duplicate-last）规则存在块数歧义：`[a,b,c]` 与
  `[a,b,c,c]` 的根逐字节相同，且 3 块集合的证明可通过 4 块集合的校验；
  提升规则使二者根不同、证明不可复用（测试见
  `TestRootBindsBlockCount`、`TestProofCannotCrossSets`）。
- **根分裂**：追加使叶子数超过当前树容量时自动长高一层，仍只重算 O(log n)。
- **下标越界**：`prove`/`update` 抛 `IndexError`。
- **类型安全**：数据块须为 `bytes` 类；证明/根格式非法抛 `ProofFormatError`。
