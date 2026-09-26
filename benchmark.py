"""修复前后命中率与误命中次数对比。

运行：python3 benchmark.py

方法：用固定随机种子生成一条请求流。每个"语义意图"（ground truth）会被
反复请求，但每次渲染时随机扰动：参数顺序、不稳定字段、空值字段、默认值
显式传入。请求流中还混入：共享长前缀的超长参数意图、文本相近但语义不同
的意图对。

判定：
- 命中：缓存键已存在。
- 误命中：键已存在，但键下存储的 ground truth 与当前请求的 ground truth
  不同（即返回了错误缓存，缓存污染）。
- ground truth 用修复版 canonicalize() 定义（语义身份的唯一真相）。
"""

import random

import cache_key
import cache_key_buggy

REQUESTS = 20000
SEED = 42

DEFAULTS = {"page": 1, "filter": {"sort": "asc"}}


def build_intents():
    intents = []
    words = ["phone", "laptop", "手机", "耳机", "camera", "平板"]
    for i in range(40):
        intents.append({
            "q": words[i % len(words)] + str(i % 7),
            "page": 1 + (i % 3),
            "filter": {"sort": "asc", "color": ["red", "blue"][i % 2]},
            "城市": "上海" if i % 2 else "北京",
        })
    # 文本相近但语义不同的一对意图
    intents.append({"q": "x&page=1"})
    intents.append({"q": "x", "page": 1})
    # 共享长前缀的超长参数意图（触发截断碰撞）
    prefix = "p" * 300
    for tag in ("A", "B", "C"):
        intents.append({"q": prefix + "_" + tag})
    return intents


def render(intent, rng):
    """把语义意图渲染成一次真实请求：扰动顺序/不稳定字段/空值/默认值。"""
    params = dict(intent)
    if rng.random() < 0.5:
        params["page"] = params.get("page", DEFAULTS["page"])  # 显式默认值
    if rng.random() < 0.3:
        params["extra"] = rng.choice([None, "", [], {}])       # 空值
    params["timestamp"] = rng.randint(1, 10**9)                # 不稳定字段
    params["request_id"] = "r-{}".format(rng.randint(1, 10**9))
    items = list(params.items())
    rng.shuffle(items)                                         # 随机顺序
    return dict(items)


def simulate(make_key, requests):
    cache = {}
    hits = misses = false_hits = 0
    for params, truth in requests:
        key = make_key(params)
        if key in cache:
            hits += 1
            if cache[key] != truth:
                false_hits += 1
        else:
            misses += 1
            cache[key] = truth
    return hits, misses, false_hits


def main():
    rng = random.Random(SEED)
    intents = build_intents()
    requests = []
    for _ in range(REQUESTS):
        intent = rng.choice(intents)
        params = render(intent, rng)
        truth = cache_key.canonicalize(params, DEFAULTS)
        requests.append((params, truth))

    print("请求总数: {}  语义意图数: {}  种子: {}".format(REQUESTS, len(intents), SEED))
    print("{:<10} {:>8} {:>8} {:>10} {:>10}".format(
        "实现", "命中", "未命中", "命中率", "误命中"))
    for name, fn in (("修复前", cache_key_buggy.make_key),
                     ("修复后", lambda p: cache_key.make_key(p, DEFAULTS))):
        hits, misses, false_hits = simulate(fn, requests)
        print("{:<10} {:>8} {:>8} {:>9.2f}% {:>10}".format(
            name, hits, misses, 100.0 * hits / REQUESTS, false_hits))


if __name__ == "__main__":
    main()
