"""生成一份抽样校验报告样例（sample_report.txt）。

场景：100 个分片的数据集，本次有 4 个分片被合法修改，
按 20% 比例抽样复核未变化分片；其中 1 个未变化分片被旁路篡改。

运行：python3 demo_report.py
"""

import random

from incremental_checksum import ShardStore, ShardVerifier

rng = random.Random(42)
store = ShardStore.from_bytes(rng.randbytes(100 * 64), shard_size=64)
v = ShardVerifier(store)
v.commit()

# 合法修改 4 个分片
for i in [3, 17, 58, 90]:
    store.write_shard(i, rng.randbytes(64))
# 旁路篡改 1 个未变化分片（不动版本号）
store.tamper_shard(41, rng.randbytes(64))

report = v.verify(sample_ratio=0.2, seed=20260926)

lines = [
    "========== 增量校验抽样报告 ==========",
    "总分片数        : %d" % report.total_shards,
    "变化分片(必查)  : %d" % report.changed_shards,
    "抽样复核分片    : %d" % report.sampled_unchanged,
    "本次实际检查    : %d" % report.checked_shards,
    "未覆盖分片      : %d" % report.uncovered_shards,
    "抽样覆盖率      : %.2f%%" % (report.coverage * 100),
    "不一致分片      : %s" % (report.mismatches or "无"),
    "校验结论        : %s" % report.conclusion,
    "======================================",
]
text = "\n".join(lines)
print(text)
with open("sample_report.txt", "w") as f:
    f.write(text + "\n")
