# -*- coding: utf-8 -*-
"""修复前后长文本分块耗时对比。

运行：python3 benchmark.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import chunker
import chunker_buggy


def build_text(target_len):
    """构造混合中英文 + 变音符号 + 表情序列的长文本。"""
    block = (
        "你好世界hello世界123"
        "café naïve "
        "👨‍👩‍👧👍🏽🇨🇳"
        "混合mixed内容content"
    )
    repeats = target_len // len(block) + 1
    return (block * repeats)[:target_len]


def time_it(fn, *args, repeat=5, **kwargs):
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn(*args, **kwargs)
        best = min(best, time.perf_counter() - start)
    return best


def main():
    sizes = [50_000, 200_000, 800_000]
    print("%-12s %-14s %-14s %-14s %-14s" % (
        "码位数", "缺陷版(码位)", "修复版(字素)", "修复版(宽度)", "修复版(字节)"))
    for n in sizes:
        text = build_text(n)
        chunk_size = 100
        t_buggy = time_it(chunker_buggy.chunk_text, text, chunk_size)
        t_cp = time_it(chunker.chunk_text, text, chunk_size)
        t_w = time_it(chunker.chunk_text, text, chunk_size, strategy="width")
        t_b = time_it(chunker.chunk_text, text, chunk_size, strategy="bytes")
        print("%-12d %-14s %-14s %-14s %-14s" % (
            n,
            "%.2f ms" % (t_buggy * 1e3),
            "%.2f ms (%.1fx)" % (t_cp * 1e3, t_cp / t_buggy),
            "%.2f ms (%.1fx)" % (t_w * 1e3, t_w / t_buggy),
            "%.2f ms (%.1fx)" % (t_b * 1e3, t_b / t_buggy),
        ))
        # 顺带验证修复版在该规模下的内容守恒
        assert "".join(chunker.chunk_text(text, chunk_size)) == text


if __name__ == "__main__":
    main()
