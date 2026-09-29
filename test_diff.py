"""对拍测试：快速实现 vs 最近合法码字搜索参考实现。

覆盖：
  - 数据模式：全零、全一、一组确定性随机数据
  - 编码一致性：两种实现产出完全相同的码字
  - 无错译码：结论为 no_error 且数据还原
  - 每个单比特翻转位置：两侧结论一致，且必须是 corrected、位置正确、数据还原
  - 三比特翻转穷举：每个 k 穷举全部 C(n,3) 种翻转组合；凡标注 corrected
    的结果必须位置合法（0 <= pos < n）且纠正后的码字重新校验通过
    （重编码比对 + 再次译码为 no_error）。n 较小时与参考实现逐组合对拍，
    n 较大时另抽样对拍
  - 随机抽样的多比特翻转组合（2..6 位）：两侧结论必须一致；
    其中双比特翻转按 SECDED 性质必须判为 uncorrectable（拒绝输出）

运行：python3 test_diff.py
退出码非 0 表示发现不一致。
"""

import itertools
import random
import sys

from secded import (
    CORRECTED,
    NO_ERROR,
    UNCORRECTABLE,
    code_params,
    codeword_bits,
    decode,
    encode,
    ref_decode,
    ref_encode,
)

K_LIST = [1, 2, 3, 4, 5, 7, 8, 11, 16, 26, 32, 57, 64, 120, 128]
RANDOM_DATA_PER_K = 1
MULTI_FLIP_SAMPLES = 100   # 每个 (k, 数据模式) 抽样的多比特组合数
TRIPLE_FLIP_REF_MAX_N = 22  # n <= 此值时三比特翻转与参考实现逐组合对拍
TRIPLE_FLIP_REF_SAMPLES = 50  # n 更大时每个 (k, 数据模式) 抽样对拍的组合数


def data_patterns(k, rng):
    patterns = [0, (1 << k) - 1]
    for _ in range(RANDOM_DATA_PER_K):
        patterns.append(rng.getrandbits(k))
    return patterns


def check_one(k, data, rng, stats):
    n = codeword_bits(k)
    cw_fast = encode(data, k)
    cw_ref = ref_encode(data, k)
    assert cw_fast == cw_ref, (k, data, cw_fast, cw_ref)
    stats["encode"] += 1

    # 无错
    r_fast = decode(cw_fast, k)
    r_ref = ref_decode(cw_fast, k)
    assert r_fast == r_ref, (k, data, "clean", r_fast, r_ref)
    assert r_fast.status == NO_ERROR and r_fast.data == data
    stats["decode"] += 2

    # 全部单比特翻转位置
    for pos in range(n):
        flipped = cw_fast ^ (1 << pos)
        r_fast = decode(flipped, k)
        r_ref = ref_decode(flipped, k)
        assert r_fast == r_ref, (k, data, pos, r_fast, r_ref)
        assert r_fast.status == CORRECTED, (k, data, pos, r_fast)
        assert r_fast.error_position == pos, (k, data, pos, r_fast)
        assert r_fast.data == data, (k, data, pos, r_fast)
        stats["decode"] += 2
        stats["single_flip"] += 1

    # 随机多比特翻转组合
    max_w = min(6, n)
    for _ in range(MULTI_FLIP_SAMPLES):
        w = rng.randint(2, max_w)
        positions = rng.sample(range(n), w)
        flipped = cw_fast
        for p in positions:
            flipped ^= 1 << p
        r_fast = decode(flipped, k)
        r_ref = ref_decode(flipped, k)
        assert r_fast == r_ref, (k, data, positions, r_fast, r_ref)
        assert_corrected_valid(r_fast, flipped, n, k)
        if w == 2:
            # SECDED 性质：双比特错必须被检测为不可纠，绝不允许误纠
            assert r_fast.status == UNCORRECTABLE, (k, data, positions, r_fast)
            assert r_fast.data is None
        stats["decode"] += 2
        stats["multi_flip"] += 1


def assert_corrected_valid(res, received, n, k):
    """凡标注 corrected 的结果：位置必须合法，且纠正后的码字必须重新校验通过。"""
    if res.status != CORRECTED:
        return
    pos = res.error_position
    assert pos is not None and 0 <= pos < n, (res, n)
    corrected = received ^ (1 << pos)
    # 重编码比对：纠正后的码字必须是合法码字
    assert encode(res.data, k) == corrected, (res, corrected)
    # 再次译码：必须通过校验，判为无错且数据一致
    again = decode(corrected, k)
    assert again.status == NO_ERROR and again.data == res.data, (res, again)


def check_triple_flips(k, data, rng, stats):
    """穷举全部 C(n,3) 种三比特翻转。

    缩短汉明码上三比特翻转可能产生超出合法位置区段的校正子，
    译码器必须拒绝而非误纠。每个组合都跑快速实现并校验 corrected
    结果的自洽性；n 较小时逐组合与参考实现对拍，n 较大时抽样对拍。
    """
    n = codeword_bits(k)
    cw = encode(data, k)
    exhaustive_ref = n <= TRIPLE_FLIP_REF_MAX_N
    sampled_ref = set()
    if not exhaustive_ref:
        for _ in range(TRIPLE_FLIP_REF_SAMPLES):
            sampled_ref.add(tuple(sorted(rng.sample(range(n), 3))))
    for positions in itertools.combinations(range(n), 3):
        flipped = cw
        for p in positions:
            flipped ^= 1 << p
        r_fast = decode(flipped, k)
        assert_corrected_valid(r_fast, flipped, n, k)
        stats["decode"] += 1
        if exhaustive_ref or positions in sampled_ref:
            r_ref = ref_decode(flipped, k)
            assert r_fast == r_ref, (k, data, positions, r_fast, r_ref)
            stats["decode"] += 1
        stats["triple_flip"] += 1


def main():
    rng = random.Random(20260926)
    stats = {"encode": 0, "decode": 0, "single_flip": 0, "multi_flip": 0, "triple_flip": 0}
    for k in K_LIST:
        params = code_params(k)
        for data in data_patterns(k, rng):
            check_one(k, data, rng, stats)
            check_triple_flips(k, data, rng, stats)
        print(
            f"k={k:4d}  n={params['codeword_bits']:4d}  "
            f"r={params['hamming_parity_bits']:2d}+1  OK"
        )
    print(
        "PASS: encode 对拍 {encode} 次, decode 比对 {decode} 次 "
        "（单比特翻转 {single_flip} 组, 三比特翻转穷举 {triple_flip} 组, "
        "多比特翻转 {multi_flip} 组）".format(**stats)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
