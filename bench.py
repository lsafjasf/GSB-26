"""吞吐与内存基准：AC 自动机 vs 朴素逐词扫描。

运行：python3 bench.py [--text-len N] [--repeat N]
"""

import argparse
import gc
import random
import time
import tracemalloc

from tests.diff_test import gen_text, gen_vocab, naive_find_all

from sensitive_matcher import SensitiveMatcher, normalize

NORM_OPTS = dict(lowercase=True, fullwidth_to_halfwidth=True,
                 strip_whitespace=True, strip_zero_width=True)


def build_matcher(vocab):
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    m = SensitiveMatcher(**NORM_OPTS)
    m.add_all(vocab)
    m.build()
    build_s = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return m, build_s, peak / 1e6


def ac_scan(m, text, repeat):
    best = float("inf")
    hits = 0
    for _ in range(repeat):
        t0 = time.perf_counter()
        hits = sum(1 for _ in m.finditer(text))
        best = min(best, time.perf_counter() - t0)
    return best, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-len", type=int, default=1_000_000)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    rng = random.Random(42)
    # 真实场景：长文本中命中稀疏（5% 埋词）
    text = gen_text(rng, gen_vocab(rng, 200), args.text_len, plant_prob=0.05)
    text_mb = len(text.encode("utf-8")) / 1e6
    print(f"文本: {len(text)} 字符 / {text_mb:.2f} MB "
          f"（归一化: 大小写+全半角+空白+零宽全开）\n")
    print(f"{'词数':>7} {'构建ms':>8} {'内存MB':>7} {'AC ms':>8} "
          f"{'AC MB/s':>8} {'朴素s':>8} {'朴素MB/s':>9} {'加速比':>7} {'命中':>7}")

    for n_words in (1000, 5000, 20000):
        vocab = gen_vocab(random.Random(1000 + n_words), n_words)
        m, build_s, mem_mb = build_matcher(vocab)
        ac_s, hits = ac_scan(m, text, args.repeat)
        t0 = time.perf_counter()
        naive_hits = len(naive_find_all(text, vocab, NORM_OPTS))
        naive_s = time.perf_counter() - t0
        assert hits == naive_hits, (hits, naive_hits)
        print(f"{len(vocab):>7} {build_s*1e3:>8.1f} {mem_mb:>7.2f} "
              f"{ac_s*1e3:>8.1f} {text_mb/ac_s:>8.1f} {naive_s:>8.2f} "
              f"{text_mb/naive_s:>9.2f} {naive_s/ac_s:>6.0f}x {hits:>7}")


if __name__ == "__main__":
    main()
