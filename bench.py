"""吞吐基准与编码开销表。

用法：
    python3 bench.py            # 开销表 + 吞吐基准
    python3 bench.py --quick    # 减少样本，快速跑一遍
"""

import random
import sys
import time

from secded import CORRECTED, code_params, decode, encode

K_LIST = [8, 16, 32, 64, 128, 256, 512, 1024]


def overhead_table():
    print("== 编码开销 vs 理论下界 ==")
    print(
        f"{'k':>5} {'r(SEC下界)':>9} {'+整体校验':>8} {'n':>5} "
        f"{'开销位':>6} {'开销占比':>8} {'未用校正子':>9}"
    )
    for k in K_LIST:
        p = code_params(k)
        print(
            f"{p['data_bits']:>5} {p['hamming_parity_bits']:>9} "
            f"{p['overall_parity_bits']:>8} {p['codeword_bits']:>5} "
            f"{p['overhead_bits']:>6} {p['overhead_ratio']*100:>7.2f}% "
            f"{p['unused_syndromes']:>9}"
        )
    print("说明：r 是单比特纠错的理论下界（Hamming 界，2^r >= k+r+1）；")
    print("多出的 1 位是整体奇偶校验位，用于把 dmin 从 3 提到 4，换来双比特检测。")
    print()


def _time_decode(codewords, k, repeat):
    best = 0.0
    for _ in range(repeat):
        t0 = time.perf_counter()
        for cw in codewords:
            decode(cw, k)
        dt = time.perf_counter() - t0
        best = max(best, len(codewords) / dt)
    return best


def bench(k, nblocks, repeat):
    rng = random.Random(1234)
    n = code_params(k)["codeword_bits"]
    blocks = [rng.getrandbits(k) for _ in range(nblocks)]
    t0 = time.perf_counter()
    encoded = [encode(d, k) for d in blocks]
    enc_rate = nblocks / (time.perf_counter() - t0)

    clean_rate = _time_decode(encoded, k, repeat)

    corrupted = [cw ^ (1 << rng.randrange(n)) for cw in encoded]
    corr_rate = _time_decode(corrupted, k, repeat)
    # 确认全部按预期纠正
    for cw, want in zip(corrupted[:256], blocks[:256]):
        res = decode(cw, k)
        assert res.status == CORRECTED and res.data == want

    return enc_rate, clean_rate, corr_rate


def main():
    quick = "--quick" in sys.argv
    nblocks = 5000 if quick else 20000
    repeat = 1 if quick else 3
    overhead_table()
    print(f"== 吞吐基准（每档 {nblocks} 块，取 {repeat} 次最好值）==")
    print(
        f"{'k':>5} {'n':>5} {'编码 块/秒':>14} "
        f"{'解码(无错) 块/秒':>16} {'解码(纠1位) 块/秒':>17}"
    )
    for k in K_LIST:
        enc, clean, corr = bench(k, nblocks, repeat)
        n = code_params(k)["codeword_bits"]
        print(f"{k:>5} {n:>5} {enc:>14,.0f} {clean:>16,.0f} {corr:>17,.0f}")


if __name__ == "__main__":
    main()
