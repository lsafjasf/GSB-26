"""生成基准数据集：确定性 LCG，分片 CSV 文件。

用法: python3 gen_bench.py [--rows N] [--shards K] [--dir DIR] [--skew]
默认: 1,000,000 行 / 16 分片 / data/bench / 倾斜分布
"""

import argparse
import os

from qengine import Schema, write_csv_shard
from qengine.queries import EVENTS_COLS

SCHEMA = Schema(EVENTS_COLS)
REGIONS = ["east", "west", "south", "north"]
STATUSES = ["ok", "err", "retry"]


def _lcg(seed):
    state = seed
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        yield state >> 16


def gen_rows(n, skew, seed=987654321):
    rng = _lcg(seed)
    rows = []
    for i in range(n):
        r = next(rng)
        if skew:
            # 用户倾斜：平方分布，头部用户占大头（模拟真实倾斜）
            user = 1 + int(((r % 1000) / 1000.0) ** 2 * 49999)
            # 区域倾斜：east 约 50%
            region = REGIONS[0] if (r >> 8) % 2 == 0 else REGIONS[1 + (r >> 16) % 3]
        else:
            user = 1 + r % 50000
            region = REGIONS[(r >> 8) % 4]
        status = STATUSES[0] if (r >> 12) % 10 < 8 else STATUSES[1 + (r >> 20) % 2]
        amount = (r >> 24) % 10000
        if (r >> 34) % 97 == 0:
            amount = None
        score = ((r >> 36) % 100000) / 100.0
        if (r >> 44) % 89 == 0:
            score = None
        rows.append({"user_id": user, "region": region, "amount": amount,
                     "score": score, "status": status, "ts": i + 1})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--shards", type=int, default=16)
    ap.add_argument("--dir", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "bench"))
    ap.add_argument("--skew", action="store_true", default=True)
    ap.add_argument("--uniform", dest="skew", action="store_false")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    q, rem = divmod(args.rows, args.shards)
    pos = 0
    for i in range(args.shards):
        size = q + (1 if i < rem else 0)
        rows = gen_rows(size, args.skew, seed=987654321 + i)
        path = os.path.join(args.dir, f"shard-{i:03d}.csv")
        write_csv_shard(path, SCHEMA, rows)
        pos += size
        print(f"  {path}  {size} rows")
    with open(os.path.join(args.dir, "manifest.txt"), "w") as f:
        f.write(f"rows={args.rows}\nshards={args.shards}\nskew={args.skew}\n")
    print(f"done: {args.rows} rows x {args.shards} shards -> {args.dir}")


if __name__ == "__main__":
    main()
