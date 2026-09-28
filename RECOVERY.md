# 守护进程状态恢复协议

## 提交协议（写入侧）

`StateStore.commit()` 按固定顺序执行 6 个可枚举阶段（`COMMIT_STAGES`），
任意阶段被真实 SIGKILL 强杀都只可能留下旧状态或新状态之一，不会产生混合状态：

1. `serialize` — 序列化 `{magic, version, seq, payload}` 并计算 sha256
2. `tmp_written` / `tmp_fsynced` — 写入 `<path>.tmp`、flush、fsync
3. `bak_rotated` — 当前主文件轮换为 `<path>.bak`（tmp + fsync + rename）
4. `renamed` — `os.replace(tmp, path)`，**原子提交点**
5. `dir_fsynced` — fsync 目录，保证 rename 持久

### 逐阶段强杀验证（一条命令）

```bash
python3 kill_matrix.py            # 临时目录跑完即删，失败时退出码非 0
python3 kill_matrix.py DIR        # 每个阶段一个 case-* 子目录，保留现场
```

矩阵对每个阶段执行真实进程实验（不是 `os._exit` 自裁）：

- 先由一个真实子进程提交完整 OLD 状态（seq=1，counter=1）；
- 再由第二个真实子进程提交内容完全不同的 NEW 状态（seq=2，counter=2，
  待处理/已完成 id 均与 OLD 不交叉）；
- 子进程通过 `GSB_SYNC_DIR` 会合点在每个阶段原子发布阶段号并阻塞等待，
  父进程在目标阶段从外部投递 **SIGKILL**（断言 `returncode == -9`、
  实际命中阶段 == 目标阶段）；
- 强杀后审计磁盘裸字节：主文件 / `.bak` / `.tmp` 分别是 OLD、NEW、
  无效还是不存在（如 `renamed` 前主文件必须仍是 OLD，tmp 是 NEW）；
- 用两个相互独立的恢复子进程各跑一次完整恢复管道，断言：
  - 内容逐字节精确匹配：恢复结果的 sha256 摘要 == OLD 摘要（`renamed`
    之前）或 NEW 摘要（`renamed` 及之后），不是只看计数器；
  - seq 符合边界（前 4 个阶段为 1，后 2 个为 2）；
  - 不变量全部成立：`counter == len(items)`、`items` 是从 1 开始的
    稠密连续无重复序号、待处理 id 无重复、待处理与已完成集合不交叉；
  - 恢复阶段顺序固定为 `locate → … → ready`；
  - 两次恢复的 seq/摘要/磁盘结果完全一致（恢复幂等，第二跑无副作用）。

断言结果打印成一张对照表（见 `kill_matrix_output.txt`），任何阶段出现
混合状态、摘要不符、不变量破坏或两轮恢复不一致，该阶段判 FAIL 且整体
退出码为非 0。

单元测试侧（`TestPerStageKill`）同样改为外部真实 SIGKILL 会合驱动，并
用 `test_full_kill_matrix_matches_boundary_table` 整张表做断言。

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
cd /home/administrator/gsb/uid158/B
python3 kill_matrix.py                    # 逐阶段强杀对照表（一键复跑）
python3 -m unittest -v test_daemon_state.py
```
