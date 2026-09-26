#!/usr/bin/env python3
"""大规模对拍 + 吞吐/内存基准。

流程：
1. 可复现地生成 ``--words`` 个随机敏感词（英文随机串 + 中文词，刻意制造
   公共前缀以拉开 trie 规模）与长度约 ``--text`` 码点的文本，其中按
   ``--density`` 随机插入真实词，保证有足够命中；
2. 分别用 Aho-Corasick 引擎与朴素逐词扫描（str.find 逐词扫全文）跑，
   逐 (词, 起点, 终点) 比较命中集合，不一致即退出码 1；
3. 计时（每侧取 best-of-N），用 tracemalloc 测建库峰值内存，报告吞吐。

用法：
    python3 bench/benchmark.py                  # 默认 10000 词 / 500K 字符
    python3 bench/benchmark.py --words 50000 --text 2000000 --runs 5
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sensitive import Normalizer, SensitiveEngine, naive_find_all  # noqa: E402

EN_CHARS = "abcdefghijklmnopqrstuvwxyz"
CJK_CHARS = "的一是不了人我在有他这为之大来以个中上们到说时地也子就道会那"


def generate_words(rng, n_en: int, n_cjk: int) -> list[str]:
    words: set[str] = set()
    # 公共前缀池，刻意制造前缀重叠（也是最容易退化的场景）
    prefixes = ["".join(rng.choice(EN_CHARS) for _ in range(rng.randrange(1, 4)))
                for _ in range(32)]
    while len(words) < n_en:
        body = "".join(rng.choice(EN_CHARS) for _ in range(rng.randrange(3, 10)))
        if rng.random() < 0.3:
            body = rng.choice(prefixes) + body
        words.add(body)
    cjk: set[str] = set()
    while len(cjk) < n_cjk:
        cjk.add("".join(rng.choice(CJK_CHARS) for _ in range(rng.randrange(2, 6))))
    return sorted(words) + sorted(cjk)


def generate_text(rng, words: list[str], length: int, density: float) -> str:
    """density: 每个文本 token 是“真实敏感词”的概率。"""
    planted = [w for w in words if all(ord(c) < 128 for c in w) or len(w) >= 2]
    parts: list[str] = []
    size = 0
    while size < length:
        if rng.random() < density:
            token = rng.choice(planted)
        else:
            n = rng.randrange(4, 16)
            token = "".join(
                rng.choice(EN_CHARS + "   ") if rng.random() < 0.8 else rng.choice(CJK_CHARS)
                for _ in range(n)
            )
        parts.append(token)
        size += len(token)
    text = "".join(parts)
    return text[:length]


def hit_key_set(hits):
    return {(h.word, h.start, h.end) for h in hits}


def best_time(fn, runs: int) -> tuple[float, object]:
    best = float("inf")
    result = None
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        result = fn()
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best, result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20240926)
    ap.add_argument("--words", type=int, default=10000, help="敏感词总数")
    ap.add_argument("--cjk-words", type=int, default=2000)
    ap.add_argument("--text", type=int, default=500_000, help="文本码点数")
    ap.add_argument("--density", type=float, default=0.10)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    import random

    rng = random.Random(args.seed)
    words = generate_words(rng, args.words - args.cjk_words, args.cjk_words)
    text = generate_text(rng, words, args.text, args.density)
    text_bytes = len(text.encode("utf-8"))

    print("=" * 68)
    print(f"词典: {len(words)} 词   文本: {len(text):,} 码点 / {text_bytes/1e6:.2f} MB(UTF-8)")
    print("=" * 68)

    # --- 建库计时 + 内存 ---
    gc.collect()
    t0 = time.perf_counter()
    tracemalloc.start()
    engine = SensitiveEngine(words)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    build_dt = time.perf_counter() - t0
    print(
        f"建库 AC : {build_dt:7.3f} s | trie 节点 {engine.node_count:,} | "
        f"峰值内存 {peak / 1e6:7.2f} MB（含去重后 {engine.word_count:,} 词）"
    )

    # --- AC 扫描 ---
    ac_dt, ac_hits = best_time(lambda: engine.find_all(text), args.runs)
    print(
        f"扫描 AC : {ac_dt:7.3f} s | 命中 {len(ac_hits):>7,} | "
        f"吞吐 {len(text) / ac_dt / 1e6:6.2f} M 码点/s | "
        f"{text_bytes / ac_dt / 1e6:6.2f} MB/s(UTF-8)"
    )

    # --- 朴素逐词扫描 ---
    nv_dt, nv_hits = best_time(lambda: naive_find_all(text, words), args.runs)
    print(
        f"扫描朴素: {nv_dt:7.3f} s | 命中 {len(nv_hits):>7,} | "
        f"吞吐 {len(text) / nv_dt / 1e6:6.2f} M 码点/s | "
        f"{text_bytes / nv_dt / 1e6:6.2f} MB/s(UTF-8)"
    )

    # --- 对拍 ---
    ok = hit_key_set(ac_hits) == hit_key_set(nv_hits)
    print("-" * 68)
    print(f"命中集合对拍: {'✅ 完全一致' if ok else '❌ 不一致'}")
    print(f"加速比: {nv_dt / ac_dt:8.1f}×")
    if not ok:
        a, b = hit_key_set(ac_hits), hit_key_set(nv_hits)
        only_ac = sorted(a - b)[:5]
        only_nv = sorted(b - a)[:5]
        print("仅 AC 命中:", only_ac)
        print("仅朴素命中:", only_nv)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
