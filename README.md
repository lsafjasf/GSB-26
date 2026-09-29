# lease_lock —— 基于文件的互斥锁 + 租约续期 + 代际 fencing

纯 Python 3 标准库实现（POSIX，依赖 `fcntl.flock`），用于单机上多进程互斥访问资源。
持锁进程被强杀后，租约到期即可被安全抢占；代际编号（fencing token）防止旧持有者
“复活”后继续操作。

## 文件

- `lease_lock.py` —— 库：`LeaseLock`（加锁/续期/释放/自动续期）、`FencedResource`（资源侧代际 fencing）
- `lock_inspector.py` —— 锁目录巡检：识别过期租约 / 内容损坏 / 孤儿锁，输出判定依据与建议动作
- `test_lease_lock.py` —— 自测：16 个用例，含代际安全、强杀恢复时间线、争抢耗时分布
- `test_lock_inspector.py` —— 巡检自测：12 个用例，含 dry-run 安全性、清理后立即可加锁且代际连续

## 运行

```bash
python3 -m unittest -v test_lease_lock   # 或: python3 test_lease_lock.py
python3 -m unittest -v test_lock_inspector
```

## 锁目录巡检（lock_inspector）

崩溃 / 强杀会留下三类问题锁文件，巡检器逐文件给出判定类别、可核对的判定依据
（内容校验、代际对比、修改时间、持有者进程存活）与建议动作：

| 判定 | 依据 | 建议动作 |
| --- | --- | --- |
| 有效租约 valid | 内容合法、未过期、持有进程存活 | 等待 wait |
| 过期租约 expired | `expires_at` 已过 | 抢占 preempt |
| 孤儿锁 orphan | 持有者 `host:pid` 中 pid 在本机已不存在（`kill(pid,0)` 探测） | 清理 cleanup |
| 内容损坏 corrupt | JSON 解析失败 / 缺必需字段；mtime + 租约周期未过时保守等待 | 等待 / 清理 |

```bash
python3 lock_inspector.py <锁目录>                 # 默认 dry-run，只预览不修改
python3 lock_inspector.py <锁目录> --json          # JSON Lines，便于逐条核对 / 脚本消费
python3 lock_inspector.py <锁目录> --execute       # 执行建议动作
```

- **只预览不修改**：默认 dry-run；巡检本身只读。
- **执行安全**：`--execute` 在 guard 临界区内复检，状态已变化（如锁被重建）则跳过；
  清理只删除锁文件本体，代际 sidecar（`<lock>.gen`）始终保留，
  因此清理后新的加锁能立即成功且代际连续（sidecar_gen + 1）。
- **可逐条核对**：每个文件的结论都附带完整 evidence（内容校验结果、锁文件/sidecar
  代际、mtime、expires_at、pid 存活探测），文本与 `--json` 输出一致。

## 锁文件格式（JSON）

```json
{"holder": "host:pid:rand", "token": "uuid", "generation": 7,
 "lease_duration": 5.0, "renewed_at": 169..., "expires_at": 169...}
```

## 设计要点

- **临界区**：获取/抢占/续期/释放的读-改-写由 `<lock>.guard` 上的 `flock(LOCK_EX)`
  保护；flock 由内核在进程死亡（含 SIGKILL）时自动释放，guard 永不残留。
- **租约**：`expires_at` 到期后其他进程可抢占，代际 `generation` 单调 +1；
  代际同时持久化在 `<lock>.gen` sidecar，锁文件损坏时代际仍可延续。
- **强杀恢复**：持有者被 kill -9 → 租约到期 → 他人抢占，见测试输出的时间线。
- **旧持有者防护**：续期/释放前校验 `token + generation`，不匹配抛 `PreemptedError`
  并主动放弃（清空本地状态，绝不静默继续）；`FencedResource` 在资源侧持久化
  已见最高代际，拒绝更低代际的写入（`StaleGenerationError`），是最后一道防线。
- **续期失败可识别**：锁文件被删（`LockLostError`）、内容被改/损坏
  （`PreemptedError`/`LockLostError`）、时钟回拨/跳变/文件 mtime 来自未来
  （`ClockDriftError`）；续期写后还会回读校验，防止临界区内被并发篡改。
- **损坏文件**：无法解析的锁文件保守等待一个完整租约周期（按 mtime）后才允许抢占。
- **时钟可注入**：`clock=`/`monotonic=` 参数便于测试时钟漂移场景。

## 最小示例

```python
from lease_lock import LeaseLock, FencedResource, LockLostError

lock = LeaseLock("/tmp/res.lock", lease_duration=5.0)
lock.acquire(timeout=10.0)
lock.start_auto_renew(on_lost=lambda e: print("锁丢失，停止操作:", e))
try:
    FencedResource("/tmp/res.data").write(lock, "payload\n")
finally:
    lock.stop_auto_renew()
    lock.release()
```
