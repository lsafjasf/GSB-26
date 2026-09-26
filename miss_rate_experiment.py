"""漏检率实验：人工注入局部篡改，测量漏检率与抽样比例的关系。

模型：N 个分片中随机篡改 k 个（旁路篡改，不动版本号）。
增量校验对未变化分片按比例 r 抽样，篡改被漏检的概率理论值为 (1-r)^k。
实验对每组 (r, k) 重复 trials 次，统计实测漏检率并与理论值对照。

运行：python3 miss_rate_experiment.py
输出：stdout 表格 + miss_rate_data.csv
"""

import csv

from incremental_checksum import MISMATCH, ShardStore, ShardVerifier

N = 1000          # 分片总数
TRIALS = 2000     # 每组重复次数
RATIOS = [0.0, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]
TAMPER_COUNTS = [1, 3, 10]


def miss_rate(ratio: float, k: int, trials: int) -> float:
    misses = 0
    for t in range(trials):
        store = ShardStore.from_bytes(bytes(N), shard_size=1)
        v = ShardVerifier(store)
        v.commit()
        # 固定种子可复现：每个 trial 篡改不同分片
        import random
        rng = random.Random(t * 7919 + k)
        for i in rng.sample(range(N), k):
            store.tamper_shard(i, b"\xff")
        report = v.verify(sample_ratio=ratio, seed=t)
        if report.conclusion != MISMATCH:
            misses += 1
    return misses / trials


def main() -> None:
    rows = []
    header = ["sample_ratio", "tampered_shards", "trials",
              "measured_miss_rate", "theoretical_miss_rate"]
    print(f"{'抽样比例r':>10} {'篡改分片k':>9} {'实测漏检率':>10} "
          f"{'理论漏检率(1-r)^k':>18}")
    for k in TAMPER_COUNTS:
        for r in RATIOS:
            measured = miss_rate(r, k, TRIALS)
            theory = (1 - r) ** k
            rows.append([r, k, TRIALS, f"{measured:.4f}", f"{theory:.4f}"])
            print(f"{r:>10.2f} {k:>9} {measured:>10.4f} {theory:>18.4f}")
    with open("miss_rate_data.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print("\n已写出 miss_rate_data.csv")


if __name__ == "__main__":
    main()
