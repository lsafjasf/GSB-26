# Merkle 哈希树库（Python 3，仅标准库）

用于集合完整性校验的哈希树（Merkle Tree）：从数据块集合构建树、计算根摘要、
为任一数据块生成包含证明；验证方只需根摘要 + 数据块 + 证明路径即可校验。
支持 O(log n) 增量更新（修改/追加）。

## 文件

| 文件 | 说明 |
|---|---|
| `merkle.py` | 库源码（构建、根摘要、证明生成/验证、增量更新） |
| `test_merkle.py` | 自测：23 个用例，含严格拒绝场景与边界情形 |
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
   （carry-up，与 RFC 6962 一致），不与自身配对。落单节点在证明中不产生
   元素，验证方按同一规则重算，保证构建与验证一致。由此根摘要与块数
   一一对应：「n 块」与「n 块再加一份重复的末块」得到不同的根，
   证明无法在不同块数的集合间复用（旧 duplicate-last 规则存在该歧义）。
3. **空集合**：根摘要为常量 `EMPTY_ROOT = SHA256(b"MHT/empty")`，树高 0，
   不存在包含证明。空集合根与"空数据块"（`b""`）的叶子哈希不同，互不混淆。
4. **单数据块**：根即叶子哈希本身，树高 0，证明为空列表。
5. **证明格式**：`[(direction, sibling_hash), ...]`，自叶子向根排列；
   `direction='L'` 表示兄弟在左，`'R'` 表示兄弟在右。方向编码进证明，
   验证无需额外传下标。落单提升的层不产生证明元素，故证明长度 ≤ 树高。

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
| 修改 1 块 | ~7.8 µs/次 | ~80,885 µs/次 | ~10,400× |
| 追加 1 块 | ~8.1 µs/次 | ~78,523 µs/次 | ~9,700× |

增量更新复杂度 O(log n)（每步 1 次哈希），整树重建 O(n)。

## 证明长度与树高关系

树高 == `ceil(log2 n)`（n ≥ 2；n = 1 时为 0）；证明长度 ≤ 树高
（落单提升的层不产生证明元素，末叶子的证明可能更短）：

| 块数 n | 树高（最大证明长度） | 证明大小（1 字节方向 + 32 字节哈希）/元素 |
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
  奇偶变化时，受影响父节点（含旧的被提升节点）都在重算路径上。
- **根分裂**：追加使叶子数超过当前树容量时自动长高一层，仍只重算 O(log n)。
- **下标越界**：`prove`/`update` 抛 `IndexError`。
- **类型安全**：数据块须为 `bytes` 类；证明/根格式非法抛 `ProofFormatError`。
