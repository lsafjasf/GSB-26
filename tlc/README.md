# 线程本地缓存 + 全局合并存储（Python 3，仅标准库）

高频写走线程本地 delta 缓存（写路径不碰共享锁），按需把整批 deltas
幂等合并进全局计数；全局读返回某一时刻的一致快照。

## 文件

- `tlc_store.py` — 库：`ThreadLocalMergeStore` / `_LocalCache`
- `test_tlc_store.py` — 一致性断言测试 + 合并幂等测试（8 个用例）
- `bench.py` — 吞吐与延迟基准（含并发读/写/合并）
- `BENCHMARKS.md` / `bench_output.txt` — 实测数据

## 运行命令

```bash
cd tlc
python3 test_tlc_store.py          # 全部断言测试
python3 bench.py                   # 基准（默认 8 线程 x 200k 写）
python3 bench.py 500000 16         # 自定义：每线程写数 / 线程数
```

## 一致性设计

- **一致的全局视图**：一批本地 deltas 在全局锁内一次性应用，读也在同一把
  锁内拷贝；全局读要么看到整批、要么看不到，不存在半批可见。
  `snapshot_with_total()` 一次持锁返回 `(视图, 总量)`，避免分两次读跨批次。
- **幂等合并**：每个本地源(source)有单调递增 `seq`，全局侧维护每个 source
  的高水位。重复提交同一 `(source_id, seq, deltas)` 直接忽略（返回 False），
  绝不重复计数。`test_merge_idempotent_low_level` 对同一批重复合并 100 次
  做断言。
- **线程退出策略：合并（merge-on-exit），不丢弃**。线程应在 finally 里调
  `close()` 把本地缓存合入并注销；即使忘记调用，store 会在每次全局读/合并时
  通过 `_reap_dead()`（对 owner 线程的 weakref + `is_alive()`）自动合入死
  线程遗留的本地缓存。选「合入」是因为本存储是单调计数器，静默丢弃会丢数、
  违反调用方对「不丢不重」的预期；确需丢弃请显式调 `discard_local()`（只丢
  未合并的本地增量，不影响全局）。
- **不变量**（`assert_invariants()`）：
  1. 全局总量 == 各 source 已合并量之和 == 全局 map 各 key 之和；
  2. 总量任意时刻单调不减（无删除 API；显式删除不在本实现范围，
     `discard_local` 只作用于尚未合并的本地部分）。
- **动态线程**：新线程首次写入时自动注册并获得新 source id；线程消失后其
  缓存被 reaper 合入并继续留在注册表中（seq 高水位保留，保证迟到的重放批次
  仍然幂等）。

## API 速览

```python
store = ThreadLocalMergeStore()
store.add("key", 1)     # 线程本地写（无争用）
store.flush()           # 合并本线程本地缓存，返回新合入量
store.flush_all()       # 合并所有本地缓存（含死线程遗留）
store.snapshot()        # 一致视图 dict 拷贝
store.snapshot_with_total()   # (视图, 总量) 原子读
store.total(); store.get("key")
store.close()           # 线程退出时合并并注销（建议放 finally）
store.discard_local()   # 显式丢弃本线程未合并增量
store.assert_invariants()      # 断言总量守恒与单调
```
