# 守护进程状态恢复协议

## 提交协议（写入侧）

`StateStore.commit()` 按固定顺序执行，任意阶段强杀都不会产生混合状态：

1. `serialize` — 序列化 `{magic, version, seq, payload}` 并计算 sha256
2. `tmp_written` / `tmp_fsynced` — 写入 `<path>.tmp` 并 fsync
3. `bak_rotated` — 当前主文件轮换为 `<path>.bak`（tmp + fsync + rename）
4. `renamed` — `os.replace(tmp, path)`，**原子提交点**
5. `dir_fsynced` — fsync 目录，保证 rename 持久

强杀断言（`TestPerStageKill.test_kill_at_every_commit_stage` 逐阶段验证）：

- 在 `renamed` 之前强杀 → 恢复到**旧的完整状态**（seq=1）
- 在 `renamed` 及之后强杀 → 恢复到**新的完整状态**（seq=2）
- 任何阶段都不会出现半新半旧的混合状态（sha256 校验 + 结构不变量双重保证）

## 恢复协议（启动侧）

恢复阶段按固定顺序执行，每个阶段幂等（重复执行结果相同）：

| 阶段 | 动作 | 幂等性 |
|---|---|---|
| `locate` | 定位状态文件，清理残留 `.tmp` | 删除不存在的 tmp 是 no-op |
| `parse` | JSON 解码，失败判为损坏 | 只读 |
| `validate` | 校验 magic + sha256 | 只读 |
| `classify` | 版本分类：旧版本迁移；未知新版本抛 `UnknownVersionError` | 迁移可重入，原文件备份为 `.migrated-from-v1` |
| `fallback` | 主文件损坏 → 保留为 `*.corrupt.<ts>`，回退 `.bak`，再退化为空状态 | 重命名只发生一次 |
| `ready` | 返回状态 | — |

## 损坏 vs 版本不认识

- **损坏**（解析失败 / 校验和不匹配 / 截断）：原文件改名为 `*.corrupt.<时间戳>` 保留供排查，回退备份或空状态。
- **版本不认识**（version > 当前支持版本）：抛 `UnknownVersionError`，**原文件原地保留、不做任何改名**，与损坏路径完全区分。
- **旧版本**：按迁移链（v1→v2）升级，不误判为损坏；迁移前原文件备份为 `.migrated-from-v1`。

## 清理任务的 exactly-once（效果层面）

- 清理动作本身必须幂等（默认实现：删除已不存在的文件是 no-op）。
- 每完成一个任务，先把 `completed_cleanups` 落盘，再处理下一个。
- 重启后已完成的任务不会重跑（完成记录已持久化）。
- 若在「动作执行后、落盘前」强杀，动作会重放一次，但因幂等无副作用
  （`test_cleanup_idempotent_when_killed_before_persist` 验证真实副作用恰好一次）。

## 运行

```bash
cd /home/administrator/gsb/uid178/B
python3 -m unittest -v test_daemon_state.py
```
