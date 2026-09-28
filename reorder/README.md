# 消息重排缓冲：缺陷修复与回归测试

纯 Python 3 标准库实现，无第三方依赖。

## 文件

- `reorder_buffer_buggy.py` — 现网版本（含四类缺陷），仅用于复现
- `reorder_buffer.py` — 修复版
- `test_reorder.py` — 复现用例（Part A）+ 回归测试（Part B）

## 运行

```bash
cd reorder
python3 -m unittest test_reorder -v
```

18 个用例全部通过：4 个复现用例（针对现网版本，稳定复现缺陷）+
14 个修复版回归用例（顺序/完整性断言、回绕边界、缓冲上界、内存峰值、
缺口计时独立起算）。

## 修复前后窗口推进逻辑的差异

**修复前（reorder_buffer_buggy.py）**

- 交付只认 `seq == next` 精确匹配，`next` 缺失则缓冲无限积压（缺陷1）；
- 无判重：`seq <= next` 一律直接交付，重复消息交付两次（缺陷2）；
- 比较用普通整数 `< / <= / sorted()`，回绕后旧序号被误判为"未来"
  消息重新入缓冲（缺陷3）；
- 超时释放只 `sorted(buf)` 清空交付，**不推进 `next` 基线**，
  已交付序号再次到达会被重新缓冲、第二次交付（缺陷4）。

**修复后（reorder_buffer.py）**

- 基线 `next_expected` 是唯一的真实性来源，**单调前进（模序号空间），
  只增不减**：
  1. 正常交付：`next_expected` 命中缓冲 → 交付并 `+1`，连续排空；
  2. 空洞超时：`next_expected` 缺失且缓冲非空即计时，超过
     `gap_timeout` → 记录 `GapEvent([next_expected, min_buffered - 1])`，
     把基线**直接推进到最小缓冲序号**继续交付（缺口被显式报告，
     不会被静默吞掉）；**每个缺口独立起算**——计时锚点 `_gap_since`
     只属于当前缺口，基线一旦向前推进（缺口被跳过或被迟到消息补齐）
     即清除，新缺口重新等待完整 `gap_timeout`，不复用上一个缺口的时间；
  3. 缓冲溢出（`on_full="expire"`）：等价于超时立即触发，强制推进基线。
- 判重规则（RFC 1982 风格序号算术，`distance(a,b) = (b-a) mod 2^N`）：
  - 已在缓冲中 → 重复，丢弃；
  - `0 < distance(seq, next_expected) <= 2^(N-1)` → 在已交付半空间，
    重复，丢弃；
  - `distance(next_expected, seq) < max_window` → 接收窗口内，缓冲；
  - 其余 → 溢出，`reject`（默认，丢弃并计数）或 `expire`（推进基线腾位）。
- 回绕边界：距离恰为序号空间一半（`2^(N-1)`）的序号是 RFC 1982 的
  歧义点，本实现**约定判为已交付侧（丢弃）**，有边界测试锁定该行为
  （`test_wraparound_boundary_half_space`）。

**为什么不会再次死锁**：基线推进只有两个来源——命中交付（`+1`）和
空洞跳过（跳到最小缓冲序号），二者都使基线严格前进且缓冲单调消耗；
空洞计时器在缓冲非空且基线缺失时必然启动，超时必然触发跳过，
因此"基线缺失 + 缓冲非空"是**有界等待**状态，不存在无限阻塞。
基线单调前进同时保证：任何已交付序号都落在已交付半空间内，
再次到达必然命中判重规则被丢弃——不可能再次交付（缺陷4 根除）。

## 缓冲上界与内存数据

- 任意时刻缓冲条目数 `<= max_window`（构造时校验
  `max_window < 2^(mod_bits-1)`，否则回绕判重无法良定义）；
- 回归实测（`test_memory_peak`，10 万条 64 字节消息、每 1000 条一个
  永久空洞、`max_window=256`）：
  - 缓冲占用峰值 **255 条**（上界 256）；
  - `tracemalloc` 内存峰值 **54.0 KiB**；
  - 空洞跳过 4 次，全部显式报告为 `GapEvent`；
- 溢出策略可配置：`reject`（默认，拒绝并计 `dropped`）或
  `expire`（强制推进基线，缺口计入 `gaps`）。

## 顺序与完整性断言

- `assert_strictly_increasing`：按序号算术验证交付序列严格递增；
- `test_out_of_order_completeness`：5000 条块内乱序 + 重复注入，
  交付序列必须**恰好等于** `0..N`（不重不漏）；
- `test_no_redelivery_after_timeout_release`：超时释放后重发全部
  已交付/已跳过序号，零交付、全部计入 `duplicates`。
