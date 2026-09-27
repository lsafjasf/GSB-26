"""bitset_algebra: 位集合代数库（双表示自适应）。

元素为 [0, universe) 内的非负整数。两种内部表示：

- bitmap   : Python 任意精度 int，第 i 位表示元素 i。适合高密度。
             内存 ~ universe/8 字节；集合运算退化为 C 级机器字运算 O(universe/64)。
- interval : 规范化有序区间列表 [(lo, hi), ...]（闭区间，升序、互不相交、
             互不相邻、无空区间）。适合稀疏/成段场景。
             内存 ~ runs * BYTES_PER_INTERVAL；运算为区间扫描 O(runs)。

表示选择依据（_prefer_bitmap）：比较两种表示的估计内存，
bitmap 内存 = universe/8，interval 内存 = runs * BYTES_PER_INTERVAL，
取较小者。混合表示运算用代价模型 _plan() 选择更省的执行路径。
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from typing import Iterable, Iterator, List, Optional, Tuple

Interval = Tuple[int, int]

# CPython 3.12 实测：list 槽位 8B + tuple 56B + 两个 int 各 28B。
BYTES_PER_INTERVAL = 8 + 56 + 2 * 28

# 混合运算路径代价模型（同一 arbitrary 单位）：
# bitmap 路径按字操作计（C 速度），interval 路径按 Python 级步进计。
_WORD_OP_COST = 1
_PY_STEP_COST = 8


def _estimate_bitmap_bytes(span: int) -> int:
    return (span + 7) // 8 + 64


def _estimate_interval_bytes(runs: int) -> int:
    return 64 + runs * BYTES_PER_INTERVAL


def _prefer_bitmap(span: int, runs: int) -> bool:
    """判定依据：哪种表示估计占用内存更小就用哪种。"""
    return _estimate_bitmap_bytes(span) <= _estimate_interval_bytes(runs)


def _normalize(intervals: Iterable[Interval]) -> List[Interval]:
    """规范化为：升序、互不相交、互不相邻、无空区间。"""
    cleaned = [(lo, hi) for lo, hi in intervals if lo <= hi]
    if not cleaned:
        return []
    cleaned.sort()
    out: List[Interval] = []
    lo, hi = cleaned[0]
    for l, h in cleaned[1:]:
        if l <= hi + 1:  # 重叠或相邻 -> 合并
            if h > hi:
                hi = h
        else:
            out.append((lo, hi))
            lo, hi = l, h
    out.append((lo, hi))
    return out


def _bits_to_intervals(bits: int) -> List[Interval]:
    out: List[Interval] = []
    pos = 0
    x = bits
    while x:
        tz = (x & -x).bit_length() - 1  # 跳过连续 0
        pos += tz
        x >>= tz
        run = ((x ^ (x + 1)) >> 1).bit_length()  # 连续 1 的长度
        out.append((pos, pos + run - 1))
        pos += run
        x >>= run
    return out


def _intervals_to_bits(ivs: List[Interval]) -> int:
    bits = 0
    for lo, hi in ivs:
        bits |= ((1 << (hi - lo + 1)) - 1) << lo
    return bits


def _iv_union(a: List[Interval], b: List[Interval]) -> List[Interval]:
    return _normalize(a + b)


def _iv_intersection(a: List[Interval], b: List[Interval]) -> List[Interval]:
    out: List[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _iv_difference(a: List[Interval], b: List[Interval]) -> List[Interval]:
    out: List[Interval] = []
    j = 0
    for lo, hi in a:
        cur = lo
        while j < len(b) and b[j][1] < cur:
            j += 1
        k = j
        while k < len(b) and b[k][0] <= hi:
            if b[k][0] > cur:
                out.append((cur, b[k][0] - 1))
            cur = max(cur, b[k][1] + 1)
            if cur > hi:
                break
            k += 1
        if cur <= hi:
            out.append((cur, hi))
    return out


def _iv_symmetric_difference(a: List[Interval], b: List[Interval]) -> List[Interval]:
    # (a-b) 与 (b-a) 互不相交，但可能相邻，需重新规范化
    return _normalize(_iv_difference(a, b) + _iv_difference(b, a))


def _iv_issubset(a: List[Interval], b: List[Interval]) -> bool:
    j = 0
    for lo, hi in a:
        while j < len(b) and b[j][1] < lo:
            j += 1
        if j == len(b) or b[j][0] > lo or b[j][1] < hi:
            return False
    return True


class BitSet:
    """[0, universe) 上的整数集合，自动选择 bitmap / interval 表示。"""

    __slots__ = ("_bits", "_ivs", "_universe")

    def __init__(self, universe: int, bits: Optional[int] = None,
                 ivs: Optional[List[Interval]] = None):
        if universe < 0:
            raise ValueError("universe must be >= 0")
        if (bits is None) == (ivs is None):
            raise ValueError("exactly one representation required")
        self._universe = universe
        self._bits = bits
        self._ivs = ivs

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def empty(cls, universe: int = 0) -> "BitSet":
        return cls(universe, ivs=[])

    @classmethod
    def full(cls, universe: int) -> "BitSet":
        return cls.from_intervals([(0, universe - 1)], universe=universe)

    @classmethod
    def from_ints(cls, values: Iterable[int], universe: Optional[int] = None) -> "BitSet":
        vals = sorted(set(values))
        if any(v < 0 for v in vals):
            raise ValueError("elements must be >= 0")
        if universe is None:
            universe = vals[-1] + 1 if vals else 0
        if vals and vals[-1] >= universe:
            raise ValueError("element out of universe bound")
        runs: List[Interval] = []
        for v in vals:
            if runs and v == runs[-1][1] + 1:
                runs[-1] = (runs[-1][0], v)
            else:
                runs.append((v, v))
        if _prefer_bitmap(universe, len(runs)):
            return cls(universe, bits=_intervals_to_bits(runs))
        return cls(universe, ivs=runs)

    @classmethod
    def from_intervals(cls, intervals: Iterable[Interval],
                       universe: Optional[int] = None) -> "BitSet":
        ivs = _normalize(intervals)
        if ivs and ivs[0][0] < 0:
            raise ValueError("intervals must be >= 0")
        if universe is None:
            universe = ivs[-1][1] + 1 if ivs else 0
        if ivs and ivs[-1][1] >= universe:
            raise ValueError("interval out of universe bound")
        if _prefer_bitmap(universe, len(ivs)):
            return cls(universe, bits=_intervals_to_bits(ivs))
        return cls(universe, ivs=ivs)

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------
    @property
    def universe(self) -> int:
        return self._universe

    @property
    def representation(self) -> str:
        return "bitmap" if self._bits is not None else "interval"

    def count(self) -> int:
        if self._bits is not None:
            return self._bits.bit_count()
        return sum(hi - lo + 1 for lo, hi in self._ivs)

    def density(self) -> float:
        return self.count() / self._universe if self._universe else 0.0

    def runs(self) -> int:
        if self._ivs is not None:
            return len(self._ivs)
        return len(_bits_to_intervals(self._bits))

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, v: int) -> bool:
        if v < 0 or v >= self._universe:
            return False
        if self._bits is not None:
            return (self._bits >> v) & 1 == 1
        ivs = self._ivs
        i = bisect_right(ivs, (v, sys.maxsize)) - 1
        return i >= 0 and ivs[i][0] <= v <= ivs[i][1]

    def __iter__(self) -> Iterator[int]:
        if self._bits is not None:
            x = self._bits
            while x:
                lsb = x & -x
                yield lsb.bit_length() - 1
                x ^= lsb
        else:
            for lo, hi in self._ivs:
                yield from range(lo, hi + 1)

    def to_set(self) -> set:
        return set(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BitSet):
            return NotImplemented
        return self._canonical() == other._canonical()

    def _canonical(self) -> Tuple[Interval, ...]:
        return tuple(self._as_ivs())

    def __repr__(self) -> str:
        return (f"BitSet(universe={self._universe}, count={self.count()}, "
                f"repr={self.representation})")

    # ------------------------------------------------------------------
    # 表示转换 / 路径选择
    # ------------------------------------------------------------------
    def _as_bits(self) -> int:
        return self._bits if self._bits is not None else _intervals_to_bits(self._ivs)

    def _as_ivs(self) -> List[Interval]:
        return self._ivs if self._ivs is not None else _bits_to_intervals(self._bits)

    def as_representation(self, kind: str) -> "BitSet":
        """强制转换为指定表示（'bitmap' 或 'interval'），主要用于测试/基准。"""
        if kind == "bitmap":
            return BitSet(self._universe, bits=self._as_bits())
        if kind == "interval":
            return BitSet(self._universe, ivs=self._as_ivs())
        raise ValueError(kind)

    def optimize(self) -> "BitSet":
        """按当前密度重新选择表示。"""
        runs = self.runs()
        want_bitmap = _prefer_bitmap(self._universe, runs)
        if want_bitmap == (self._bits is not None):
            return self
        return self.as_representation("bitmap" if want_bitmap else "interval")

    def _plan(self, other: "BitSet") -> str:
        """混合表示运算的路径选择：估计两条路径的代价，取较小者。

        bitmap 路径代价 ~ span/64 个 C 级字运算；
        interval 路径代价 ~ 总区间数个 Python 级步进。
        bitmap 一侧的区间数未知，用 min(count, span) 作上界估计。
        """
        span = max(self._universe, other._universe)
        bits_cost = (span // 64 + 1) * _WORD_OP_COST
        ivs_cost = (self._run_estimate() + other._run_estimate()) * _PY_STEP_COST
        return "bits" if bits_cost <= ivs_cost else "ivs"

    def _run_estimate(self) -> int:
        if self._ivs is not None:
            return len(self._ivs)
        return min(self._bits.bit_count(), max(1, self._universe // 2))

    # ------------------------------------------------------------------
    # 集合运算
    # ------------------------------------------------------------------
    def _binary(self, other: "BitSet", op: str) -> "BitSet":
        universe = max(self._universe, other._universe)
        if self._bits is not None and other._bits is not None:
            plan = "bits"
        elif self._ivs is not None and other._ivs is not None:
            plan = "ivs"
        else:
            plan = self._plan(other)

        if plan == "bits":
            a, b = self._as_bits(), other._as_bits()
            if op == "or":
                r = a | b
            elif op == "and":
                r = a & b
            elif op == "diff":
                r = a & ~b
            elif op == "xor":
                r = a ^ b
            else:  # pragma: no cover
                raise ValueError(op)
            return BitSet(universe, bits=r)

        a, b = self._as_ivs(), other._as_ivs()
        if op == "or":
            r = _iv_union(a, b)
        elif op == "and":
            r = _iv_intersection(a, b)
        elif op == "diff":
            r = _iv_difference(a, b)
        elif op == "xor":
            r = _iv_symmetric_difference(a, b)
        else:  # pragma: no cover
            raise ValueError(op)
        return BitSet(universe, ivs=r)

    def union(self, other: "BitSet") -> "BitSet":
        return self._binary(other, "or")

    def intersection(self, other: "BitSet") -> "BitSet":
        return self._binary(other, "and")

    def difference(self, other: "BitSet") -> "BitSet":
        return self._binary(other, "diff")

    def symmetric_difference(self, other: "BitSet") -> "BitSet":
        return self._binary(other, "xor")

    __or__ = union
    __and__ = intersection
    __sub__ = difference
    __xor__ = symmetric_difference

    def issubset(self, other: "BitSet") -> bool:
        if self._bits is not None and other._bits is not None:
            return self._bits & ~other._bits == 0
        if self._ivs is not None and other._ivs is not None:
            return _iv_issubset(self._ivs, other._ivs)
        if self._plan(other) == "bits":
            return self._as_bits() & ~other._as_bits() == 0
        return _iv_issubset(self._as_ivs(), other._as_ivs())

    __le__ = issubset

    # ------------------------------------------------------------------
    # 上界变化
    # ------------------------------------------------------------------
    def with_universe(self, new_universe: int) -> "BitSet":
        """调整上界：扩界免费；缩界需要截断（bitmap 掩码 / interval 裁剪）。

        截断后重新评估表示（密度可能显著变化）。
        """
        if new_universe < 0:
            raise ValueError("universe must be >= 0")
        if self._bits is not None:
            bits = self._bits
            if new_universe < self._universe:
                bits &= (1 << new_universe) - 1
            return BitSet(new_universe, bits=bits).optimize()
        ivs = self._ivs
        if new_universe < self._universe:
            clipped: List[Interval] = []
            for lo, hi in ivs:
                if lo >= new_universe:
                    break
                clipped.append((lo, min(hi, new_universe - 1)))
            ivs = clipped
        return BitSet(new_universe, ivs=ivs).optimize()

    # ------------------------------------------------------------------
    # 不变量
    # ------------------------------------------------------------------
    def assert_invariants(self) -> None:
        """断言内部不变量；供测试与调试使用。"""
        assert (self._bits is None) != (self._ivs is None), "exactly one repr"
        assert self._universe >= 0
        if self._bits is not None:
            assert self._bits >= 0
            assert self._bits >> self._universe == 0, "bits beyond universe"
            return
        prev_hi = -2
        for lo, hi in self._ivs:
            assert lo <= hi, f"empty interval {(lo, hi)}"
            assert lo >= 0, "negative interval"
            assert hi < self._universe, "interval beyond universe"
            assert lo > prev_hi + 1, f"intervals not disjoint/non-adjacent at {(lo, hi)}"
            prev_hi = hi
