"""百万条记录的多键排序性能实测。"""

import random
import time

from multisort import KeySpec, sort_multi


def gen(n, seed=42):
    rng = random.Random(seed)
    depts = ["dept-%02d" % i for i in range(20)]
    rows = []
    for i in range(n):
        rows.append({
            "id": i,
            "dept": rng.choice(depts),
            "score": round(rng.uniform(0, 100), 2) if rng.random() > 0.1 else None,
            "name": "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=12)),
        })
    return rows


def verify_sorted(rows, keys):
    """独立校验：逐对比较相邻记录，并抽查稳定性。"""
    def key_of(r):
        out = []
        for spec in keys:
            v = r[spec.key]
            out.append(v)
        return out
    for prev, cur in zip(rows, rows[1:]):
        for spec in keys:
            a, b = prev[spec.key], cur[spec.key]
            if a is None or b is None:
                if a is None and b is None:
                    continue
                null_first = a is None
                ok = null_first if spec.nulls == "first" else not null_first
            else:
                ok = a <= b if not spec.reverse else a >= b
                if a == b:
                    continue
            assert ok, "排序结果不正确: %r vs %r" % (prev, cur)
            break
        else:
            continue
    # 稳定性抽查：完全同键的记录 id 必须递增
    seen = {}
    for r in rows:
        k = tuple(r[s.key] for s in keys)
        if k in seen:
            assert r["id"] > seen[k], "稳定性被破坏"
        seen[k] = r["id"]


def main():
    n = 1_000_000
    keys = [
        KeySpec("dept"),
        KeySpec("score", reverse=True, nulls="last"),
        KeySpec("name"),
    ]
    print("生成 %d 条记录 ..." % n)
    t0 = time.perf_counter()
    rows = gen(n)
    print("生成耗时: %.2f s" % (time.perf_counter() - t0))

    t0 = time.perf_counter()
    out = sort_multi(rows, keys)
    dt = time.perf_counter() - t0
    print("sort_multi（3 键，含全序校验）: %.2f s  ->  %.0f 条/秒" % (dt, n / dt))

    t0 = time.perf_counter()
    out2 = sort_multi(rows, keys, validate=False)
    dt2 = time.perf_counter() - t0
    print("sort_multi（3 键，跳过校验）  : %.2f s  ->  %.0f 条/秒" % (dt2, n / dt2))

    t0 = time.perf_counter()
    verify_sorted(out, keys)
    print("结果校验（有序性 + 稳定性抽查）通过，耗时: %.2f s" % (time.perf_counter() - t0))

    assert [r["id"] for r in out] == [r["id"] for r in out2]
    print("校验开关两种路径结果一致")


if __name__ == "__main__":
    main()
