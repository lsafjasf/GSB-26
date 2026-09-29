# filelease — 基于文件的互斥锁 + 租约续期 + 代际 fencing

仅依赖 Python 3 标准库（Linux/Unix，需 `fcntl`）。

## 设计

- **锁文件** `<path>`（JSON，原子替换写入）：`holder`（持有者标识）、
  `generation`（代际编号 / fencing token）、`issued_at`、`ttl`、`expires_at`、`state`。
- **守卫文件** `<path>.guard` 上的 `flock` 串行化锁文件的读-改-写；
  进程死亡（含 `kill -9`）时内核自动释放 flock，守卫永不卡死，
  活性由租约到期时间保证。
- **代际旁车文件** `<path>.gen`：持久化最大代际，锁文件被删/损坏后代际仍单调递增。
- **过期抢占**：`expires_at <= now` 或状态为 released 时，其他进程可获取，
  代际 = max(旧代际, 旁车代际) + 1。
- **续期校验**：文件被删 → `LeaseLost`；内容被改/被抢占 → `LeaseStolen`；
  已过期 → `LeaseExpired`；墙钟与单调钟偏差超阈值（含时钟回拨）→ `ClockDriftDetected`。
  任一失败都会作废租约句柄，持有者必须放弃，不得静默继续。
- **fencing token**：`lease.fencing_token`（即代际编号）应随每次受保护写入传给下游资源，
  下游只接受严格递增的 token，从而拒绝「被抢占的旧持有者」的写入。
- **可观测性（可选）**：`FileLeaseLock(..., report=True)` 把获取/续期/释放/抢占/损坏隔离
  事件以 JSONL 追加到 `<path>.events`。事件与锁文件状态变更在同一把 flock 守卫内
  写入，且每个状态事件内嵌当时落盘的 record 快照，因此报告数据可与锁文件逐条对数。
  默认关闭，关闭时零开销；开启不改变加解锁语义与租约时长
  （事件写失败会 fail-closed 抛出，保证日志与锁文件不背离）。

## API 速览

```python
from filelease import FileLeaseLock, LeaseLost

lock = FileLeaseLock("/var/lock/res.lock", ttl=10.0)
lease = lock.acquire(timeout=30)          # 阻塞获取；try_acquire() 为非阻塞
stop, lost, _ = lease.auto_renew(on_lost=lambda e: print("租约丢失:", e))
try:
    if lost.is_set():
        raise RuntimeError("租约已丢，停止操作")
    downstream.write(lease.fencing_token, payload)   # 下游校验 token
finally:
    stop.set()
    lease.release()                        # 或 with lock.acquire() as lease: ...
```

## 运行

```bash
python3 -m unittest test_filelease -v   # 全部自测（含代际安全/强杀/争抢/回拨/损坏）
python3 -m unittest test_leasereport -v # 报告功能自测（事件/对数/时间范围/强杀回收）
python3 demo.py                          # 强杀恢复时间线 + 争抢耗时分布 + 租约报告演示
```

## 租约使用报告

```bash
python3 leasereport.py <锁文件路径> [--start TS] [--end TS] [--json] [--check]
```

- **报告内容**：加锁等待时长分布（min/p50/p90/p99/max/mean 与超时次数）、
  续期频率与续期时剩余租约、抢占事件与代际变化序列、
  持锁进程异常退出（未释放即过期）后的回收次数与「过期→回收」延迟。
- **按时间范围导出**：`--start/--end` 接受 epoch 秒或 ISO 8601
  （如 `2026-09-29T12:00:00`）；`--json` 导出机器可读报告。
- **可对数**：`--check` 校验事件流中最后一次状态快照与当前锁文件内容逐字段一致。

## 测试覆盖

| 测试 | 验证点 |
| --- | --- |
| `test_stale_holder_detected_after_preemption` | 旧持有者被抢占后续期/释放/带旧 token 写入全部被拒 |
| `test_tampered_content_detected` / `test_deleted_file_detected` | 锁文件被篡改/删除时续期报错 |
| `test_clock_rollback_detected_on_renew` | 时钟回拨被识别，租约句柄作废 |
| `test_sigkill_recovery_timeline` | kill -9 后租约到期才被抢占，输出时间线 |
| `test_contention_no_overlap_and_latency` | 6 进程争抢临界区零重叠，输出获取耗时分布 |
| `test_corrupted_lock_file_recovered` | 锁文件损坏被隔离，代际不回退 |
| `test_leasereport.py`（7 项） | 事件序列与锁文件对数、默认关闭零副作用、等待/超时记录、续期频率与剩余租约、时间范围过滤、过期抢占与代际变化、kill -9 后回收记录 |
