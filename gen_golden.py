"""生成 golden 对拍数据：输入分片 + 串行基准结果（expected）。

数据刻意构造以覆盖边界：
  - 分片大小倾斜：12 / 3 / 0（空分片）/ 33 行
  - 分组键含 NULL（region=None）、度量含 NULL（amount/score=None）
  - 用户倾斜：u1 约占 40%（聚合倾斜）
  - score 存在跨分片同分值（排序并列，靠行号兜底）
  - amount 含负数与 0（最值边界）

运行: python3 gen_golden.py
"""

import os

from qengine import Schema, run_serial, write_csv_shard
from qengine.queries import ALL_QUERIES, EVENTS_COLS

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "data", "golden", "input")
OUT_DIR = os.path.join(HERE, "data", "golden", "expected")

SCHEMA = Schema(EVENTS_COLS)

# 分片行数：倾斜 + 一个空分片
SHARD_SIZES = [12, 3, 0, 33]

REGIONS = ["east", "west", "south", "north", None]
STATUSES = ["ok", "err", "retry", None]


def _lcg(seed=20241004):
    state = seed
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        yield state >> 16          # 48 位有效随机位


def build_rows():
    rng = _lcg()
    rows = []
    ts = 0
    for shard_i, size in enumerate(SHARD_SIZES):
        shard = []
        for j in range(size):
            ts += 1
            r = next(rng)
            # 用户倾斜：约 40% 落 u1
            user = 1 if r % 10 < 4 else 2 + (r >> 4) % 7
            region = REGIONS[(r >> 8) % len(REGIONS)]
            status = STATUSES[(r >> 12) % len(STATUSES)]
            amount = ((r >> 16) % 41) - 10          # [-10, 30]，含负数与 0
            if (r >> 24) % 11 == 0:
                amount = None                        # 度量 NULL
            score = round(((r >> 28) % 1000) / 100.0, 2)   # 0.00..9.99
            if (r >> 40) % 7 == 0:
                score = None
            shard.append({
                "user_id": user, "region": region, "amount": amount,
                "score": score, "status": status, "ts": ts,
            })
        rows.append(shard)

    # 手工钉入的边界行（追加到最后一个分片，保证确定性）
    edge = [
        # 跨分片同分：score=9.99 与 shard0 首行同分，检验排序并列兜底
        {"user_id": 8, "region": "east", "amount": 30, "score": 9.99,
         "status": "ok", "ts": 1001},
        {"user_id": 8, "region": "east", "amount": 30, "score": 9.99,
         "status": "ok", "ts": 1002},
        # 全 NULL 度量行
        {"user_id": 9, "region": None, "amount": None, "score": None,
         "status": None, "ts": 1003},
        # amount 最小值候选（负数）
        {"user_id": 2, "region": "west", "amount": -10, "score": 0.01,
         "status": "err", "ts": 1004},
    ]
    rows[-1].extend(edge)
    return rows


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        return "|".join(_fmt(x) for x in v)
    return str(v)


def main():
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    shards = build_rows()
    paths = []
    for i, shard in enumerate(shards):
        p = os.path.join(IN_DIR, f"shard-{i:03d}.csv")
        write_csv_shard(p, SCHEMA, shard)
        paths.append(p)
    total = sum(len(s) for s in shards)
    print(f"input : {len(shards)} shards, {total} rows -> {IN_DIR}")

    for name, plan in ALL_QUERIES.items():
        rows = run_serial(paths, SCHEMA, plan)   # 串行 = 基准真值
        cols = plan.output_columns()
        out = os.path.join(OUT_DIR, f"{name}.csv")
        import csv
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([_fmt(r[c]) for c in cols])
        print(f"golden: {name:<20} {len(rows):>4} rows -> {out}")


if __name__ == "__main__":
    main()
