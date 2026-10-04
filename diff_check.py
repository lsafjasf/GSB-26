"""串行 vs 并行对拍：打印各场景、各并行度下的结果指纹与一致性结论。

结果指纹 = sha256(逐行 JSON 序列化拼接)，与行顺序相关，可验证“逐行一致”
而不只是“集合一致”。运行：python3 diff_check.py
"""

import hashlib
import json
import random
import time

from query_engine import (
    Aggregate, Filter, Query, execute_parallel, execute_serial, shard_rows,
)


def fingerprint(rows):
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def make_rows(n, seed=20261004):
    rnd = random.Random(seed)
    regions = ["cn", "us", "eu", "jp"]
    return [{
        "id": i,
        "amount": rnd.randint(0, 10_000),
        "region": rnd.choice(regions),
    } for i in range(n)]


SCAN = Query(filter=Filter("amount", "gt", 9_000),
             projection=("id", "amount", "region"))
AGG = Query(filter=Filter("amount", "ge", 1_000), aggregates=(
    Aggregate("count"), Aggregate("sum", "amount"),
    Aggregate("min", "amount"), Aggregate("max", "amount"),
    Aggregate("distinct", "region"),
))


def scenarios():
    big = make_rows(100_000)
    small = make_rows(10)
    skewed = make_rows(100_000)
    yield "small n=10", small, SCAN, {"workers": None}
    for w in (1, 2, 4, 8):
        yield f"big scan n=100000 p={w}", big, SCAN, {"workers": w}
        yield f"big agg  n=100000 p={w}", big, AGG, {"workers": w}
    # 倾斜分片：一片 99998 行，其余为空/单行
    parts = [skewed[:1], [], skewed[1:99_999], [], skewed[99_999:]]
    yield "skewed 5-shards scan", skewed, SCAN, {"partitions": parts}
    yield "skewed 5-shards agg", skewed, AGG, {"partitions": parts}
    # 单分片
    yield "single shard scan", big, SCAN, {"partitions": [big]}
    yield "single shard agg", big, AGG, {"partitions": [big]}
    # 分片数 > 行数（含全空尾部分片）
    many = shard_rows(small, 10) + [[] for _ in range(10)]
    yield "more shards than rows", small, SCAN, {"partitions": many}
    # 空数据
    yield "empty data", [], SCAN, {"workers": 4}


def main():
    width = 30
    print(f"{'scenario':<{width}} {'rows':>7} {'fingerprint':<18} match")
    print("-" * (width + 40))
    all_ok = True
    for name, rows, query, kw in scenarios():
        t0 = time.perf_counter()
        expected = execute_serial(rows, query)
        actual = execute_parallel(rows, query, **kw)
        dt = time.perf_counter() - t0
        ok = actual == expected
        all_ok &= ok
        fp = fingerprint(actual)
        print(f"{name:<{width}} {len(actual):>7} {fp:<18} "
              f"{'PASS' if ok else 'FAIL'}  ({dt * 1000:.1f} ms total)")
    print("-" * (width + 40))
    print("ALL PASS" if all_ok else "MISMATCH DETECTED")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
