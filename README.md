# GSB-26 — 消息重排缓冲修复

按序号重排乱序到达的消息并向上游按序交付。纯 Python 3 标准库。

## 文件

- `reorder_buffer_buggy.py` — 修复前版本（保留用于复现四类现网缺陷）
- `reorder_buffer.py` — 修复后实现
- `test_reorder_buffer.py` — 复现用例 + 回归测试（18 个用例）
- `memory_profile.py` — 积压与内存峰值测量

## 运行

```bash
python3 test_reorder_buffer.py -v   # 复现 4 类缺陷 + 修复后回归
python3 memory_profile.py           # 积压 / 内存峰值数据
```

## 四类缺陷的复现

`BuggyReproTest` 中每个用例稳定复现一类缺陷（断言缺陷行为发生）：

| 缺陷 | 用例 | 现象 |
|---|---|---|
| 空洞死锁 | `test_bug1_gap_blocks_forever` | seq 2 缺失后，3..9 永久阻塞 |
| 重复交付 | `test_bug2_duplicate_delivered_twice` | 重传的 seq 0 被交付两次 |
| 回绕判重失效 | `test_bug3_wraparound_breaks_dedup` | 65535 后 seq 0 被误判为旧包乱序交付，65535 重传再次交付 |
| 超时释放重交付 | `test_bug4_flush_redelivers` | `flush()` 释放后未推进 `_next`，seq 3 被再次交付 |

## 修复设计

### 窗口推进逻辑：修复前 vs 修复后

修复前：
- `_next` 只在精确匹配时 `+1`，没有任何超时/跳洞机制 → 空洞永久阻塞；
- `_next` 不回绕，且用普通整数 `<` 判断新旧 → 回绕后判重与排序全部失效；
- `flush()` 清空缓冲但不推进 `_next` → 已交付消息可被再次交付；
- 无任何判重状态 → 重复包直接再次交付。

修复后（`ReorderBuffer._drain`，reorder_buffer.py:150）：
- 所有比较改用**模运算有符号距离** `diff(a,b) = (a-b) mod 2^bits` 映射到 `[-2^(bits-1), 2^(bits-1))`，回绕安全（16 位序号，与 RTP 相同）；
- 交付循环只交付 `next_expected`，交付后 `next_expected = (seq+1) mod 2^bits`，严格单调推进；
- 空洞等待超过 `max_gap_wait` 秒（或宽度超过 `max_gap` 条）时，**显式报告缺口**（`on_gap(missing)` 回调给出完整缺失序号列表），把 `next_expected` 直接推进到最近的已缓冲序号，继续交付后续消息；
- 超时释放即上述跳洞：推进 `next_expected` 的同时完成交付，被跳过的序号之后再到达一律按"过旧"丢弃（`on_drop(seq, "duplicate")`），不会二次交付。

### 为什么不会再死锁

只要缓冲非空且 `next_expected` 缺失，跳洞条件（时间或宽度）必然在有限时间内满足：
`poll()`/`push()` 每次都会检查 `now - gap_since >= max_gap_wait`，时钟单调推进，
因此窗口下界 `next_expected` 单调不减且持续前进；缓冲最多 `window` 条，
每条要么被交付、要么被报告为缺口跳过。`test_no_deadlock_repeated_gaps`
与随机属性测试（2 万条、5% 丢失、10% 重复、乱序）验证了交付最终收敛。

### 可验证的判重规则

设 `next_expected` 为下一个待交付序号，入包序号 `s`：

- `diff(s, next_expected) < 0` → 已交付或已被跳过：**判重/过旧，丢弃**；
- `s` 已在缓冲中 → **重传，丢弃**；
- `0 <= diff < window` → 新包，缓冲或交付；
- `diff >= window` → 超出窗口：**overflow 丢弃并上报**。

边界（`test_dedup_boundary`，4 位序号便于枚举）：`diff = -1` 判重；
`diff = -8`（半空间歧义点）定义为"旧"；`diff = 0` 交付；`diff = window` overflow。
回绕边界由 `test_wraparound_*` 与 `test_full_cycle_no_duplicates`
（连续两整圈 13 万条，交付序列模意义严格递增）覆盖。

### 交付序列性质（测试断言）

- 严格递增（模意义）且不重复：`assert_strictly_increasing_mod`；
- 完整性：每条入包消息必居其一——已交付 / 已报告缺口 / 已上报丢弃
  （`test_randomized_property` 中逐项断言 `delivered ∩ gapped = ∅`、
  `lost ⊆ gapped ∪ 尾部`、`late ⊆ gapped`）。

### 缓冲上界与内存数据

缓冲条目数硬上界 = `window`（超出即 overflow 丢弃），与输入总量无关。
`python3 memory_profile.py`（20 万条、2% 丢失、抖动 ≤20、window=1024、序号回绕 3 次）实测：

```
messages pushed        : 196071 (seq wraps 3x)
delivered              : 194105
reported as gap        : 5894
dropped (dup/overflow) : 1966 (dup=1966, overflow=0)
peak backlog (entries) : 317  (window bound = 1024)
peak process alloc     : 59 KiB (tracemalloc)
buffer dict footprint  : ~160 B/entry -> worst case ~160 KiB at window=1024
```

即窗口 1024 时缓冲自身内存上界约 160 KiB（不含 payload 引用），
积压峰值 317 条，远低于上界；交付 + 缺口 + 丢弃覆盖全部输入。

### 配置说明

- `window`：窗口/积压上界，必须 `< 2^(seq_bits-1)`；
- `max_gap_wait`：空洞等待秒数，超时跳洞（默认行为：报告缺口并继续交付；
  被跳过序号的迟到消息按过旧**丢弃**并计入 `duplicate_count`）。
  设为 `None` 可关闭（退化为可阻塞模式，不推荐）；
- `max_gap`：可选，按缺口宽度（条数）跳洞；
- 锚定语义：首个入包序号锚定窗口，模意义更早的序号按旧包丢弃
  （标准 jitter buffer 行为）。

注意可解性约束：以消息条数计，`最大抖动 < 空洞超时 < 平均丢包间隔`，
否则任何实现都会在"迟到误判"与"积压增长"之间失衡（属性测试中有对应注释）。

---

另见 [CONFIG_INHERITANCE.md](CONFIG_INHERITANCE.md)：多环境配置继承系统（`config_layers.py`，base < env < local，合并可追溯）。
