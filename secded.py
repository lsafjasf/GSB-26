"""SECDED（扩展汉明码）单比特纠错 / 双比特检测库。仅使用标准库。

码结构（码字用 Python int 表示，第 j 位即比特 j）：

    比特 0        : 整体奇偶校验位（覆盖比特 1..m 的偶校验）
    比特 1..m     : 经典汉明码，m = k + r
                    位置 1, 2, 4, 8, ...（2 的幂）放校验位，其余放数据位

其中 k 为数据位宽，r 为汉明校验位数，取满足 2^r >= k + r + 1 的最小 r。
码字总长 n = k + r + 1，最小汉明距离 dmin = 4，因此可以"纠 1 检 2"。

校验位与校正子的推导
--------------------
把位置编号 1..m 写成二进制。位置 j 属于第 i 个校验组当且仅当 j 的第 i 位为 1。
第 i 个校验位（位于 2^i）取其校验组内所有比特的异或（偶校验），
于是编码后每个校验组的异或和都为 0。

译码时重新计算每个校验组的异或和，得到校正子 s（第 i 位 = 第 i 组的异或和）。
若只有位置 e 发生翻转，则第 i 组的异或和翻转当且仅当 e 的第 i 位为 1，
即 s = e —— 校正子的二进制值直接就是出错位置。s = 0 表示汉明部分无错。

整体校验位 p0 = 比特 1..m 的异或，它对任意奇数个错误都会翻转。
结合 s 与整体校验 c（整个码字的异或和）得到判决表：

    s == 0, c == 0 : 无错
    s != 0, c == 1 : 单比特错，位置 = s，翻转即纠正
    s == 0, c == 1 : 整体校验位自身出错（位置 0），翻转即纠正
    s != 0, c == 0 : 偶数个错误（>=2），不可纠，拒绝输出

本模块提供两套实现：
    encode/decode      —— 基于整数位运算与 popcount 的快速实现
    ref_encode/ref_decode —— 逐位循环的教科书式参考实现（用于对拍）
"""

from functools import lru_cache
from typing import NamedTuple, Optional

NO_ERROR = "no_error"
CORRECTED = "corrected"
UNCORRECTABLE = "uncorrectable"

_popcount = getattr(int, "bit_count", None) or (lambda x: bin(x).count("1"))


class DecodeResult(NamedTuple):
    status: str                 # NO_ERROR / CORRECTED / UNCORRECTABLE
    data: Optional[int]         # 译出的数据；不可纠时为 None
    error_position: Optional[int]  # 已纠正的码字比特位置（0 = 整体校验位）；未纠正为 None


def parity_bits_for(k: int) -> int:
    """返回保护 k 个数据位所需的汉明校验位数 r（满足 2^r >= k + r + 1 的最小 r）。"""
    if k < 1:
        raise ValueError("k must be >= 1")
    r = 0
    while (1 << r) < k + r + 1:
        r += 1
    return r


def codeword_bits(k: int) -> int:
    """码字总长 n = k + r + 1（含整体校验位）。"""
    return k + parity_bits_for(k) + 1


def code_params(k: int) -> dict:
    """返回码参数：数据位 k、汉明校验位 r、整体校验位 1、总长 n、开销等。"""
    r = parity_bits_for(k)
    n = k + r + 1
    return {
        "data_bits": k,
        "hamming_parity_bits": r,
        "overall_parity_bits": 1,
        "codeword_bits": n,
        "check_bits": r + 1,
        "overhead_bits": r + 1,
        "overhead_ratio": (r + 1) / n,
        "unused_syndromes": (1 << r) - (k + r + 1),
    }


@lru_cache(maxsize=None)
def _layout(k: int):
    """预计算：r、每个校验组的位置掩码、数据位所在位置列表。"""
    r = parity_bits_for(k)
    m = k + r
    masks = []
    for i in range(r):
        mask = 0
        for p in range(1, m + 1):
            if (p >> i) & 1:
                mask |= 1 << p
        masks.append(mask)
    data_positions = tuple(p for p in range(1, m + 1) if p & (p - 1))
    return r, tuple(masks), data_positions


def _extract_data(cw: int, data_positions) -> int:
    data = 0
    for i, p in enumerate(data_positions):
        data |= ((cw >> p) & 1) << i
    return data


# ---------------------------------------------------------------- 快速实现

def encode(data: int, k: int) -> int:
    """把 k 位数据编码为 n 位码字（整数）。"""
    if not 0 <= data < (1 << k):
        raise ValueError("data out of range for k bits")
    r, masks, data_positions = _layout(k)
    cw = 0
    for i, p in enumerate(data_positions):
        cw |= ((data >> i) & 1) << p
    for i, mask in enumerate(masks):
        if _popcount(cw & mask) & 1:
            cw |= 1 << (1 << i)
    if _popcount(cw) & 1:  # 比特 1..m 的偶校验 -> 比特 0
        cw |= 1
    return cw


def decode(cw: int, k: int) -> DecodeResult:
    """译码。返回三态结论：无错 / 已纠正（含位置）/ 不可纠（拒绝输出）。"""
    n = codeword_bits(k)
    if not 0 <= cw < (1 << n):
        raise ValueError("codeword out of range for k bits")
    r, masks, data_positions = _layout(k)
    syndrome = 0
    for i, mask in enumerate(masks):
        if _popcount(cw & mask) & 1:
            syndrome |= 1 << i
    overall = _popcount(cw) & 1  # 含比特 0 的整体校验
    if syndrome == 0 and overall == 0:
        return DecodeResult(NO_ERROR, _extract_data(cw, data_positions), None)
    if overall == 1:
        # syndrome == 0 时是整体校验位（位置 0）自己出错
        pos = syndrome
        cw ^= 1 << pos
        return DecodeResult(CORRECTED, _extract_data(cw, data_positions), pos)
    return DecodeResult(UNCORRECTABLE, None, None)


# ------------------------------------------------------- 逐位参考实现（对拍用）

def _is_pow2(x: int) -> bool:
    return x & (x - 1) == 0


def _bits_to_int(bits) -> int:
    value = 0
    for i, b in enumerate(bits):
        if b:
            value |= 1 << i
    return value


def _int_to_bits(value: int, length: int):
    return [(value >> i) & 1 for i in range(length)]


def ref_encode(data: int, k: int) -> int:
    """教科书式逐位编码：不使用任何掩码 / popcount 技巧。"""
    if not 0 <= data < (1 << k):
        raise ValueError("data out of range for k bits")
    r = parity_bits_for(k)
    m = k + r
    bits = [0] * (m + 1)  # bits[0] 为整体校验位，bits[1..m] 为汉明部分
    di = 0
    for p in range(1, m + 1):
        if not _is_pow2(p):
            bits[p] = (data >> di) & 1
            di += 1
    for i in range(r):
        parity = 0
        for p in range(1, m + 1):
            if (p >> i) & 1 and p != (1 << i):
                parity ^= bits[p]
        bits[1 << i] = parity
    overall = 0
    for p in range(1, m + 1):
        overall ^= bits[p]
    bits[0] = overall
    return _bits_to_int(bits)


def ref_decode(cw: int, k: int) -> DecodeResult:
    """教科书式逐位译码：显式重建每个校验组并逐位异或。"""
    n = codeword_bits(k)
    if not 0 <= cw < (1 << n):
        raise ValueError("codeword out of range for k bits")
    r = parity_bits_for(k)
    m = k + r
    # 长度取 2^r：多比特错误被"误纠"时 syndrome 可能超过 m，
    # 此时翻转的是一个超出有效码字范围的位置，与快速实现行为一致。
    bits = _int_to_bits(cw, 1 << r)
    syndrome = 0
    for i in range(r):
        check = 0
        for p in range(1, m + 1):
            if (p >> i) & 1:
                check ^= bits[p]
        if check:
            syndrome |= 1 << i
    overall = 0
    for p in range(0, m + 1):
        overall ^= bits[p]
    if syndrome == 0 and overall == 0:
        return DecodeResult(NO_ERROR, _ref_extract(bits, m), None)
    if overall == 1:
        pos = syndrome
        bits[pos] ^= 1
        return DecodeResult(CORRECTED, _ref_extract(bits, m), pos)
    return DecodeResult(UNCORRECTABLE, None, None)


def _ref_extract(bits, m: int) -> int:
    data = 0
    di = 0
    for p in range(1, m + 1):
        if not _is_pow2(p):
            data |= bits[p] << di
            di += 1
    return data
