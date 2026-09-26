"""修复前后缓存键命中率 / 误命中对比基准。

运行：python3 benchmark.py [请求数，默认 20000]

流量构成（固定随机种子，结果可复现）：
- 主流：200 个语义组，热度按 Zipf 倾斜；同一语义组每次到达时
  参数顺序打乱、时间戳/请求标识变化、少量请求用 NFC/NFD 两种
  Unicode 表示 —— 这些都不应该改变语义。
- 截断对抗流：5 个语义组，区分字段落在旧实现 64 字符截断窗口外，
  专门稳定复现截断碰撞。
- 注入对抗流：100 对语义不同但无分隔文本相同的请求。

判定：
- 命中   = 该键以前见过，且对应请求与首次请求语义相同；
- 误命中 = 该键以前见过，但首次请求语义不同（键污染）；
- 未命中 = 该键从未见过（首次填充缓存）。
"""

import random
import sys
import unicodedata

import cachekey
from cachekey_buggy import make_cache_key as old_key

SEED = 20260926
GROUPS = 200
CITIES = ["北京", "Shanghai", "Ko\u0308ln", "Tokyo", "Sao Paulo"]
LONG_TAIL = [
    "a=1&b=2", "c=d", "x=1&y=2", "p=q&r=s",
    "1", "1&x=2",
]


def semantic_id(params):
    """不依赖被评测实现的语义真相：经修复版规范化后按语义组判定。"""
    return cachekey.canonicalize(params)


def build_stream(n_main):
    rng = random.Random(SEED)
    stream = []

    weights = [1.0 / (i + 1) for i in range(GROUPS)]
    total = sum(weights)
    probs = [w / total for w in weights]

    for _ in range(n_main):
        gid = rng.choices(range(GROUPS), weights=probs, k=1)[0]
        city = CITIES[gid % len(CITIES)]
        if rng.random() < 0.3:
            city = unicodedata.normalize("NFD", city)
        filters = {"city": city, "page": gid % 5 + 1,
                   "tag": f"tag-{gid % 7}"}
        if rng.random() < 0.5:
            filters = dict(reversed(list(filters.items())))
        params = {
            "sort": ["asc", "desc"][gid % 2],
            "filter": filters,
            "q": f"query-{gid % 23}",
            "timestamp": rng.randrange(1_600_000_000, 1_900_000_000),
            "requestId": f"req-{rng.getrandbits(64):016x}",
        }
        if gid % 29 == 0:
            # 部分语义组携带超长参数，旧实现会截断
            params["desc"] = "d" * 40 + f"g{gid}"
        if gid % 31 == 0:
            # 部分语义组携带"看起来像分隔符"的值
            params["note"] = LONG_TAIL[gid % len(LONG_TAIL)]
        ordered = list(params.items())
        rng.shuffle(ordered)
        stream.append(("main", dict(ordered)))

    # 截断对抗流：公共前缀超过 64 字符，gid 落在截断窗口外
    adv_prefix = "p" * 70
    for gid in range(5):
        base = {"note": adv_prefix, "gid": gid}
        for _ in range(4):
            stream.append(("truncation", dict(base)))

    # 注入对抗流：(a=b,c=d) 与 (a="b&c=d") 交替
    for i in range(100):
        stream.append(("injection", {"a": "b", "c": "d", "i": i}))
        stream.append(("injection", {"a": "b&c=d", "i": i}))

    return stream


def simulate(key_fn, stream):
    seen = {}
    hits = false_hits = misses = 0
    for _source, params in stream:
        key = key_fn(params)
        truth = semantic_id(params)
        if key in seen:
            if seen[key] == truth:
                hits += 1
            else:
                false_hits += 1
        else:
            misses += 1
            seen[key] = truth
    return hits, false_hits, misses


def main():
    n_main = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    stream = build_stream(n_main)
    total = len(stream)
    distinct_truths = len({semantic_id(p) for _, p in stream})

    rows = []
    for label, fn in (("旧实现(截断/无序/含抖动)", old_key),
                      ("修复版(完整规范化+SHA256)", cachekey.digest)):
        hits, false_hits, misses = simulate(fn, stream)
        rows.append((label, hits, false_hits, misses,
                     hits / (hits + misses + false_hits) * 100))

    print(f"请求总数: {total}（主流 {n_main} + 截断对抗 20 + 注入对抗 200）")
    print(f"不同语义请求组数: {distinct_truths}")
    print("-" * 78)
    print(f"{'实现':<30}{'命中':>8}{'未命中':>8}{'误命中':>8}{'命中率':>10}")
    print("-" * 78)
    for label, hits, false_hits, misses, rate in rows:
        print(f"{label:<30}{hits:>8}{misses:>8}{false_hits:>8}"
              f"{rate:>9.2f}%")
    print("-" * 78)
    old, new = rows
    print(f"误命中变化: {old[2]} -> {new[2]}")
    print(f"命中率变化: {old[4]:.2f}% -> {new[4]:.2f}% "
          f"(+{new[4] - old[4]:.2f} 个百分点)")
    assert new[2] == 0, "修复版不允许任何误命中"
    assert new[1] == total - distinct_truths, (
        "修复版必须命中全部同语义重访请求"
    )


if __name__ == "__main__":
    main()
