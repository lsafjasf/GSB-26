# GSB-26 重放攻击防护库

Python 3 标准库实现，无第三方依赖；时间通过 `clock` 参数或 `check(..., now=...)` 注入，测试可完全控制时间。

## 文件

- `replay_protector.py` — 滑动窗口防护库
- `naive_protector.py` — 朴素全量记录参照实现（仅用于对拍）
- `test_replay_protector.py` — 单元测试（重放、边界、空窗口、倒退、跳跃、同 id 不同内容）
- `fuzz_differential.py` — 对拍脚本
- `memory_benchmark.py` — 百万请求内存基准

## 判定语义（边界均为闭区间，恰好等于允许范围时放行）

| 条件 | 结果 |
| --- | --- |
| `timestamp > now + clock_skew_seconds` | 拒绝 `too_far_future` |
| `timestamp < max_seen_ts - window_seconds` | 拒绝 `too_old` |
| `request_id` 已记录且记录时间戳在窗口内 | 拒绝 `replay`（与内容无关） |
| 其余 | 放行并记录 |

即：`timestamp == now + skew` 与 `timestamp == max_seen_ts - window` 都恰好放行。

## 清理不变式与内存

只驱逐 `recorded_ts < max_seen_ts - window` 的条目；放行下界与驱逐下界是同一表达式，
因此被驱逐的 id 若再次出现，必落入 `too_old` 分支——清理不会放行重放。

内存上界：条目数 ≤ 窗口内已放行请求数 ≤ `R * window_seconds`（R 为速率），
与请求总量无关；乱序不增加上界（乱序请求的时间戳也须落在窗口内才会被记录）。
实现采用摊还清理（存量翻倍才全量驱逐），实际 ≤ `2 * R * window + 1024` 条，
均摊 O(1) 每请求。拒绝日志为有界 `deque(maxlen=...)`。

## 运行

```bash
cd replay_guard
python3 -m unittest test_replay_protector -v   # 单元测试
python3 fuzz_differential.py 200 2000 42       # 对拍：轮数 步数 种子
python3 memory_benchmark.py 1000000            # 内存基准
```

## 用法示例

```python
from replay_protector import ReplayProtector

p = ReplayProtector(window_seconds=60, clock_skew_seconds=5)  # clock 缺省 time.time
d = p.check(request_id="msg-123", timestamp=1727400000.0)
if not d.accepted:
    ...  # d.reason in {"replay", "too_old", "too_far_future"}
```

## 基准数据（window=60s, skew=5s, 1000 条/秒, 100 万请求）

- 留存条目 63,773（理想上界 60,000，实现上界 121,024）
- tracemalloc 峰值 ≈ 13.7 MiB，与请求总量无关
- 对拍：200 轮 × 2000 步随机序列（含乱序/倒退/跳跃/重放/边界），两边拒绝集合完全一致
