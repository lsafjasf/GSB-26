"""对拍：上千词 × 长文本，Aho-Corasick 引擎与朴素逐词扫描命中集合一致。

随机 fuzz：随机词典（含互为前缀/后缀、跨字符集的词）、随机文本
（随机插入词与噪声字符），多组归一化/边界/白名单配置下逐 Hit 比较。
"""

import random
import unittest

from sensitive import SensitiveEngine, naive_find_all
from sensitive.normalize import NormalizeConfig, WS_COLLAPSE, WS_KEEP, WS_REMOVE

ALPHABET = list("abcathelo")  # 英文小字母表，提高重叠概率
CJK = list("天安门敏感词法轮功练习广场攻击攻势办证赌场賭場辦證賭辦證體門廣場攻供公事势夫")
NOISE = ["  ", "\u3000", "\u200b", "  \u200b "]  # 空白/零宽噪声


def random_word(rng: random.Random, vocab: list[str]) -> str:
    roll = rng.random()
    if roll < 0.45 and vocab:
        base = rng.choice(vocab)
        # 制造前缀/后缀包含关系
        mode = rng.randrange(3)
        if mode == 0 and len(base) > 1:
            return base[: max(1, len(base) - 1)]
        if mode == 1:
            return base + rng.choice(ALPHABET)
        return base
    pool = ALPHABET if rng.random() < 0.7 else CJK
    length = rng.randrange(1, 6)
    return "".join(rng.choice(pool) for _ in range(length))


def random_text(rng: random.Random, vocab: list[str], length: int) -> str:
    parts: list[str] = []
    size = 0
    while size < length:
        roll = rng.random()
        if roll < 0.5 and vocab:
            token = rng.choice(vocab)
        elif roll < 0.7:
            token = "".join(rng.choice(ALPHABET + CJK) for _ in range(rng.randrange(1, 8)))
        else:
            token = rng.choice(NOISE)
        parts.append(token)
        size += len(token)
    # 随机全角化/大写化个别字符
    text = "".join(parts)
    chars = list(text[:length])
    for i in range(len(chars)):
        r = rng.random()
        if r < 0.03 and chars[i] not in (" ", "\u3000", "\u200b"):
            cp = ord(chars[i])
            if 0x21 <= cp <= 0x7E:
                chars[i] = chr(cp + 0xFEE0)
        elif r < 0.06 and "a" <= chars[i] <= "z":
            chars[i] = chars[i].upper()
    return "".join(chars)


def hit_tuples(hits) -> list[tuple[str, int, int]]:
    return sorted((h.word, h.start, h.end) for h in hits)


class TestCrossCheck(unittest.TestCase):
    def test_many_configs_random(self):
        configs = [
            NormalizeConfig(),
            NormalizeConfig(casefold=False),
            NormalizeConfig(width=False),
            NormalizeConfig(whitespace=WS_REMOVE),
            NormalizeConfig(whitespace=WS_KEEP),
            NormalizeConfig(zero_width=False),
            NormalizeConfig(casefold=False, width=False, whitespace=WS_KEEP, zero_width=False),
            NormalizeConfig(t2s=False),
            NormalizeConfig(homophone=True),
            NormalizeConfig(homophone=True, t2s=True, whitespace=WS_REMOVE),
            NormalizeConfig(casefold=False, width=False, t2s=False,
                            homophone=True, whitespace=WS_KEEP, zero_width=False),
        ]
        for seed in range(60):
            rng = random.Random(seed)
            vocab_set = {random_word(rng, []) for _ in range(rng.randrange(4, 20))}
            vocab = sorted(v for v in vocab_set if v)
            words = sorted(
                {random_word(rng, vocab) for _ in range(rng.randrange(20, 120))}
            )
            words = [w for w in words if w]
            text = random_text(rng, vocab, rng.randrange(200, 1500))
            config = rng.choice(configs)
            use_boundary = rng.random() < 0.5
            whitelist = [w for w in words if rng.random() < 0.05]

            eng = SensitiveEngine(
                words,
                whitelist=whitelist,
                config=config,
                use_boundary=use_boundary,
            )
            expected = naive_find_all(
                text,
                words,
                whitelist=whitelist,
                config=config,
                use_boundary=use_boundary,
            )
            actual = eng.find_all(text)
            self.assertEqual(
                hit_tuples(actual),
                hit_tuples(expected),
                msg=(
                    f"seed={seed} use_boundary={use_boundary} "
                    f"config={config} nwords={len(words)}"
                ),
            )

    def test_large_scale_thousands_of_words(self):
        """上千词（含英文随机串与中文词）× 长文本的对拍。"""
        rng = random.Random(20240926)
        vocab = ["".join(rng.choice(ALPHABET) for _ in range(rng.randrange(2, 7)))
                 for _ in range(200)]
        words_set: set[str] = set()
        while len(words_set) < 3000:
            w = "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(2, 9)))
            words_set.add(w)
        cjk_words = {
            "".join(rng.choice(CJK) for _ in range(rng.randrange(2, 5)))
            for _ in range(500)
        }
        words = sorted(words_set | cjk_words)
        text = random_text(rng, sorted(set(vocab)) + list(cjk_words), 120_000)

        eng = SensitiveEngine(words)
        actual = hit_tuples(eng.find_all(text))
        expected = hit_tuples(naive_find_all(text, words))
        self.assertEqual(actual, expected)
        self.assertGreater(len(actual), 1000)  # 确保确实有大量命中可对拍


if __name__ == "__main__":
    unittest.main()
