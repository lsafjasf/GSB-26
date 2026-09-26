"""对拍：AC 自动机 vs 朴素逐词扫描。

随机生成上千个词 × 长文本（含 CJK/ASCII/全角/零宽/大小写），
在多种归一化选项组合下，验证两者命中集合完全一致。

运行：python3 tests/diff_test.py [--rounds N] [--seed S]
"""

import os
import random
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sensitive_matcher import SensitiveMatcher, normalize

ASCII = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CJK = [chr(c) for c in range(0x4E00, 0x4E00 + 500)]
FULLWIDTH = [chr(c) for c in range(0xFF21, 0xFF3B)]  # 全角 A-Z
NOISE = [" ", "\t", "\n", "​", "‌", "﻿", " "]


def rand_word(rng, alphabet, lo=2, hi=8):
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))


def gen_vocab(rng, n):
    vocab = set()
    while len(vocab) < n:
        kind = rng.random()
        if kind < 0.4:
            w = rand_word(rng, CJK, 2, 4)
        elif kind < 0.7:
            w = rand_word(rng, ASCII, 2, 8)
        elif kind < 0.85:
            w = rand_word(rng, FULLWIDTH, 2, 5)
        else:
            # 词内部故意掺入零宽/空白，考验归一化一致性
            w = rand_word(rng, CJK, 2, 3)
            w = w[0] + rng.choice(NOISE) + w[1:]
        vocab.add(w)
    # 故意制造"一个词是另一个词前缀"的情形
    base = list(vocab)[: n // 10]
    for w in base:
        vocab.add(w + rand_word(rng, ASCII, 1, 3))
    return sorted(vocab)


def gen_text(rng, vocab, length, plant_prob=0.35):
    """生成长文本：随机字符 + 大量埋入的词（含变体）。"""
    parts = []
    total = 0
    while total < length:
        r = rng.random()
        if r < plant_prob and vocab:
            w = rng.choice(vocab)
            if rng.random() < 0.3:  # 大小写扰动
                w = "".join(c.upper() if rng.random() < 0.5 else c for c in w)
            if rng.random() < 0.2:  # 词中插入零宽/空白
                pos = rng.randint(1, len(w) - 1) if len(w) > 1 else 1
                w = w[:pos] + rng.choice(NOISE) + w[pos:]
            parts.append(w)
        elif r < 0.5:
            parts.append(rand_word(rng, CJK, 5, 40))
        elif r < 0.7:
            parts.append(rand_word(rng, ASCII, 5, 60))
        else:
            parts.append(rng.choice(NOISE) or " ")
        total += len(parts[-1])
    return "".join(parts)


def naive_find_all(text, vocab, norm_opts):
    """朴素基线：每个词单独用 str.find 扫描归一化文本。"""
    norm_text, index_map = normalize(text, **norm_opts)
    hits = set()
    for word in vocab:
        norm_word, _ = normalize(word, **norm_opts)
        if not norm_word:
            continue
        start = 0
        while True:
            i = norm_text.find(norm_word, start)
            if i < 0:
                break
            o_start = index_map[i]
            o_end = index_map[i + len(norm_word) - 1] + 1
            hits.add((norm_word, o_start, o_end))
            start = i + 1  # 允许重叠
    return hits


def ac_find_all(text, vocab, norm_opts):
    m = SensitiveMatcher(**norm_opts)
    m.add_all(vocab)
    m.build()
    # 用归一化后的词作为 key，与朴素侧对齐
    return {(normalize(h.word, **norm_opts)[0], h.start, h.end)
            for h in m.finditer(text)}


def run_round(rng, n_words, text_len, norm_opts):
    vocab = gen_vocab(rng, n_words)
    text = gen_text(rng, vocab, text_len)
    expected = naive_find_all(text, vocab, norm_opts)
    got = ac_find_all(text, vocab, norm_opts)
    if expected != got:
        only_naive = sorted(expected - got)[:5]
        only_ac = sorted(got - expected)[:5]
        print(f"MISMATCH! opts={norm_opts}")
        print(f"  only in naive ({len(expected - got)}): {only_naive}")
        print(f"  only in AC    ({len(got - expected)}): {only_ac}")
        return False
    print(f"OK  words={len(vocab):5d} text={len(text):7d} "
          f"hits={len(got):6d} opts={norm_opts}")
    return True


def main():
    rounds = 3
    seed = 20260926
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    option_sets = [
        dict(),
        dict(lowercase=True),
        dict(lowercase=True, fullwidth_to_halfwidth=True),
        dict(lowercase=True, fullwidth_to_halfwidth=True,
             strip_whitespace=True, strip_zero_width=True),
    ]

    rng = random.Random(seed)
    ok = True
    for r in range(rounds):
        for opts in option_sets:
            ok &= run_round(rng, n_words=1500, text_len=120_000,
                            norm_opts=opts)
    print("DIFF TEST:", "ALL PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
