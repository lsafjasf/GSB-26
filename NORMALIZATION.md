# 缓存键规范化规则与回归测试说明

## 背景

原实现（`cache_key_buggy.py`）把请求参数拼接为缓存键，现网出现四类问题：

| 问题 | 根因 | 复现测试 |
| --- | --- | --- |
| 语义不同的请求共享键、互相污染 | `k=v` + `&` 拼接不转义，值中的 `&`/`=` 注入键结构 | `BuggyReproductionTest.test_bug2_*` |
| 参数顺序变化导致误未命中 | 按 dict 插入顺序拼接，不排序 | `BuggyReproductionTest.test_bug1_*` |
| 超长键截断后碰撞 | 键超过 64 字符直接截断 | `BuggyReproductionTest.test_bug3_*` |
| 不稳定字段导致命中率极低 | `timestamp`/`request_id` 等参与键 | `BuggyReproductionTest.test_bug4_*` |

## 规范化规则（修复版 `cache_key.py`）

1. **不稳定字段移除**：键名（不区分大小写）属于
   `timestamp/ts/time/request_id/req_id/trace_id/nonce/sign/signature/_`
   的参数被移除，递归作用于嵌套结构。
2. **键名**：去除首尾空白并统一小写（键名大小写不敏感）。
3. **值大小写**：字符串值保持原样，大小写属于语义（`"ABC"` ≠ `"abc"`）。
4. **空值省略**：`None`、`""`、`[]`、`{}` 被移除（递归；嵌套结构归约后
   为空也一并剪除）。
5. **默认值省略**：值等于 `defaults` 中对应默认值时移除（递归支持嵌套）。
6. **类型规范化**：`bool` → `"true"/"false"`；整数语义的 `int`/`float`
   → 十进制整数字符串（`1`、`1.0`、`"1"` 等价）；其余 `float` → `repr`。
7. **排序**：dict 按键名排序（递归）；list 保持顺序（顺序属于语义，
   `[1,2]` ≠ `[2,1]`）。
8. **序列化**：`json.dumps(sort_keys=True, ensure_ascii=False,
   separators=(",", ":"))`，结构化序列化，不存在分隔符注入歧义。
9. **长键**：canonical 串 UTF-8 超过 200 字节时，键为
   `"sha256:" + 完整内容的 SHA-256`；**绝不截断**。短键带 `v1:` 前缀，
   与哈希键命名空间隔离。

## 两条核心性质

- **语义相同 ⇒ 键相同**：排序、空值、默认值、不稳定字段、类型均已规范化
  （`KeyEquivalenceTest.test_semantic_equal_same_key`）。
- **语义不同 ⇒ 键不同**：短键即 canonical 串本身，一一对应；长键使用
  完整内容的 SHA-256，不截断，碰撞概率在生日界下约 2^-128，工程上可忽略
  （`KeyEquivalenceTest.test_semantic_diff_different_key`、
  `test_long_params_no_truncation_collision`）。

## 命中率与误命中对比

`benchmark.py` 以固定种子生成 20000 次请求（45 个语义意图，随机扰动参数
顺序、不稳定字段、空值、显式默认值，并混入共享长前缀的超长参数与文本
相近的意图对）：

| 实现 | 命中 | 未命中 | 命中率 | 误命中 |
| --- | ---: | ---: | ---: | ---: |
| 修复前 | 1265 | 18735 | 6.33% | 537 |
| 修复后 | 19955 | 45 | 99.78% | 0 |

修复后的 45 次未命中即 45 个语义意图的首次冷启动，误命中降为 0。

## 运行命令

```bash
python3 test_cache_key.py   # 复现用例 + 键等价性测试（19 个用例）
python3 benchmark.py        # 命中率与误命中对比
```

覆盖情形：空参数、单参数、嵌套结构参数、非 ASCII 参数、超长参数、
不稳定字段、参数顺序、文本相近但语义不同（见 `EdgeCaseTest` 等）。
