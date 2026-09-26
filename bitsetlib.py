"""bitsetlib —— 双表示位集合代数库（仅标准库）。

内部表示：
  * bitmap   : Python int 作为位图，第 v 位表示元素 v。内存 ~ ceil(U/8) 字节，
               与元素个数无关；运算按机器字并行（int 的 | & ^ 在 C 层按 limb 扫描）。
  * interval : 规范化有序区间元组 ((lo, hi), ...)，闭区间，升序、互不相交且
               互不相邻、无空区间。内存 ~ k * INTERVAL_BYTES，与上界 U 无关。

表示选择（判定依据）：
  位图代价只随上界 U 增长，区间代价只随区间数 k 增长，因此比较两者的
  字节模型：interval_cost(k) = k * INTERVAL_BYTES + INTERVAL_BASE_BYTES
           bitmap_cost(U)  = ceil(U / 8) + BITMAP_BASE_BYTES
  谁小用谁。k 可以精确获得：构造时归并后即为区间数；位图运算结果用
  “段起始位” 的 popcount（bits & ~(bits << 1)）一次扫描精确求出，
  只有当区间表示确实更省时才付出 O(k) 的转换代价。

混合运算的路径选择：
  * 区间 op 区间 -> 线性归并扫描，O(k1 + k2)，不触碰 U。
  * 位图 op 位图 -> 单条 int 位运算，O(U/64)，C 层完成。
  * 混合         -> 把区间一侧转成位图（O(k + U/64)）后走位图路径。
                    依据：位图运算本身是 O(U/64) 的，分段循环逐区间做
                     bigint 掩码是 O(k * U/64)，转换一次反而更省。
"""

from bisect import bisect_right

# ---- 代价模型常量（可用 bench_density.py 实测校准） ----
INTERVAL_BYTES = 112       # 每个 (lo, hi) 区间实测：tuple(56) + 两个 int(2x28) + 槽位
INTERVAL_BASE_BYTES = 96   # 空容器 + 对象头
BITMAP_BASE_BYTES = 40     # Python int 基础开销


def bitmap_cost(upper):
    return (upper + 7) // 8 + BITMAP_BASE_BYTES


def interval_cost(num_intervals):
    return num_intervals * INTERVAL_BYTES + INTERVAL_BASE_BYTES


def prefer_interval(upper, num_intervals):
    """True 表示该密度下区间表示更省内存。"""
    return interval_cost(num_intervals) < bitmap_cost(upper)


# ---------------------------------------------------------------- 区间原语

def normalize_intervals(pairs):
    """归一化：排序、去空、合并重叠与相邻区间。输入可任意乱序/重叠。"""
    ordered = sorted((lo, hi) for lo, hi in pairs if lo <= hi)
    out = []
    for lo, hi in ordered:
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def assert_normalized(ivs):
    """不变量断言：升序、互不重叠且互不相邻、无空区间。"""
    prev_hi = -2
    for lo, hi in ivs:
        assert lo <= hi, "空区间"
        assert lo > prev_hi + 1, "区间重叠或相邻"
        prev_hi = hi


def iv_union(a, b):
    out = []
    i = j = 0

    def push(lo, hi):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))

    while i < len(a) and j < len(b):
        if a[i][0] <= b[j][0]:
            push(*a[i]); i += 1
        else:
            push(*b[j]); j += 1
    while i < len(a):
        push(*a[i]); i += 1
    while j < len(b):
        push(*b[j]); j += 1
    return out


def iv_intersection(a, b):
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = a[i][0] if a[i][0] > b[j][0] else b[j][0]
        hi = a[i][1] if a[i][1] < b[j][1] else b[j][1]
        if lo <= hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def iv_difference(a, b):
    out = []
    j = 0
    nb = len(b)
    for lo, hi in a:
        cur = lo
        while j < nb and b[j][1] < cur:
            j += 1
        k = j
        while k < nb and b[k][0] <= hi:
            if cur < b[k][0]:
                out.append((cur, b[k][0] - 1))
            cur = b[k][1] + 1
            if cur > hi:
                break
            k += 1
        if cur <= hi:
            out.append((cur, hi))
    return out


def iv_symmetric_difference(a, b):
    return iv_union(iv_difference(a, b), iv_difference(b, a))


def iv_issubset(a, b):
    j = 0
    for lo, hi in a:
        while j < len(b) and b[j][1] < lo:
            j += 1
        if j == len(b) or b[j][0] > lo or b[j][1] < hi:
            return False
    return True


def iv_count(ivs):
    return sum(hi - lo + 1 for lo, hi in ivs)


# ---------------------------------------------------------------- 位图原语

def bm_from_values(values, upper):
    """O(n + U/8)：先写字节缓冲再一次转成 int，避免逐元素 bigint OR。"""
    buf = bytearray((upper + 7) // 8)
    for v in values:
        buf[v >> 3] |= 1 << (v & 7)
    return int.from_bytes(buf, "little")


def bm_from_intervals(ivs):
    bits = 0
    for lo, hi in ivs:
        bits |= ((1 << (hi - lo + 1)) - 1) << lo
    return bits


def bm_run_count(bits):
    """位图中连续 1 段（=区间数）的精确计数，单次 O(U/64) 扫描。"""
    return (bits & ~(bits << 1)).bit_count()


def bm_to_intervals(bits):
    """按段清除最低连续 1 段，O(k) 次 bigint 操作。仅在区间更省时调用。"""
    ivs = []
    while bits:
        low = bits & -bits
        start = low.bit_length() - 1
        shifted = bits >> start
        inv = ~shifted
        run = (inv & -inv).bit_length() - 1
        ivs.append((start, start + run - 1))
        bits ^= ((1 << run) - 1) << start
    return ivs


def bm_iter(bits):
    while bits:
        low = bits & -bits
        yield low.bit_length() - 1
        bits ^= low


# ---------------------------------------------------------------- 门面类型

class BitSet:
    """位集合。内部自动在 bitmap / interval 两种表示间选择。"""

    __slots__ = ("universe", "kind", "data")

    BITMAP = "bitmap"
    INTERVAL = "interval"

    def __init__(self, universe, kind, data):
        self.universe = universe
        self.kind = kind
        self.data = data

    # ---- 构造 ----

    @classmethod
    def from_ints(cls, values, universe, force=None):
        vals = sorted(set(values))
        for v in vals:
            if not 0 <= v < universe:
                raise ValueError("元素 %d 越界 [0, %d)" % (v, universe))
        runs = 0
        prev = -2
        for v in vals:
            if v > prev + 1:
                runs += 1
            prev = v
        kind = force or (cls.INTERVAL if prefer_interval(universe, runs) else cls.BITMAP)
        if kind == cls.INTERVAL:
            ivs = []
            for v in vals:
                if ivs and v == ivs[-1][1] + 1:
                    ivs[-1] = (ivs[-1][0], v)
                else:
                    ivs.append((v, v))
            return cls(universe, cls.INTERVAL, tuple(ivs))
        return cls(universe, cls.BITMAP, bm_from_values(vals, universe))

    @classmethod
    def from_intervals(cls, intervals, universe, force=None):
        ivs = normalize_intervals(intervals)
        for lo, hi in ivs:
            if lo < 0 or hi >= universe:
                raise ValueError("区间 (%d, %d) 越界 [0, %d)" % (lo, hi, universe))
        kind = force or (cls.INTERVAL if prefer_interval(universe, len(ivs)) else cls.BITMAP)
        if kind == cls.INTERVAL:
            return cls(universe, cls.INTERVAL, tuple(ivs))
        return cls(universe, cls.BITMAP, bm_from_intervals(ivs))

    @classmethod
    def empty(cls, universe):
        return cls(universe, cls.INTERVAL, ())

    @classmethod
    def full(cls, universe):
        return cls.from_intervals([(0, universe - 1)], universe) if universe else cls.empty(0)

    # ---- 结果表示决策 ----

    @classmethod
    def _from_bitmap_decided(cls, bits, universe):
        if prefer_interval(universe, bm_run_count(bits)):
            return cls(universe, cls.INTERVAL, tuple(bm_to_intervals(bits)))
        return cls(universe, cls.BITMAP, bits)

    @classmethod
    def _from_intervals_decided(cls, ivs, universe):
        if prefer_interval(universe, len(ivs)):
            return cls(universe, cls.INTERVAL, tuple(ivs))
        return cls(universe, cls.BITMAP, bm_from_intervals(ivs))

    # ---- 表示转换 ----

    def _as_bitmap(self):
        if self.kind == self.BITMAP:
            return self.data
        return bm_from_intervals(self.data)

    def _as_intervals(self):
        if self.kind == self.INTERVAL:
            return list(self.data)
        return bm_to_intervals(self.data)

    # ---- 代数运算 ----

    def _check(self, other):
        if self.universe != other.universe:
            raise ValueError("上界不一致: %d vs %d" % (self.universe, other.universe))

    def union(self, other):
        self._check(other)
        if self.kind == other.kind == self.INTERVAL:
            return self._from_intervals_decided(iv_union(self.data, other.data), self.universe)
        return self._from_bitmap_decided(self._as_bitmap() | other._as_bitmap(), self.universe)

    def intersection(self, other):
        self._check(other)
        if self.kind == other.kind == self.INTERVAL:
            return self._from_intervals_decided(iv_intersection(self.data, other.data), self.universe)
        return self._from_bitmap_decided(self._as_bitmap() & other._as_bitmap(), self.universe)

    def difference(self, other):
        self._check(other)
        if self.kind == other.kind == self.INTERVAL:
            return self._from_intervals_decided(iv_difference(self.data, other.data), self.universe)
        return self._from_bitmap_decided(self._as_bitmap() & ~other._as_bitmap(), self.universe)

    def symmetric_difference(self, other):
        self._check(other)
        if self.kind == other.kind == self.INTERVAL:
            return self._from_intervals_decided(
                iv_symmetric_difference(self.data, other.data), self.universe)
        return self._from_bitmap_decided(self._as_bitmap() ^ other._as_bitmap(), self.universe)

    def issubset(self, other):
        self._check(other)
        if self.kind == other.kind == self.INTERVAL:
            return iv_issubset(self.data, other.data)
        return self._as_bitmap() & ~other._as_bitmap() == 0

    __or__ = union
    __and__ = intersection
    __sub__ = difference
    __xor__ = symmetric_difference
    __le__ = issubset

    # ---- 观察 ----

    def count(self):
        if self.kind == self.BITMAP:
            return self.data.bit_count()
        return iv_count(self.data)

    __len__ = count

    def __iter__(self):
        if self.kind == self.BITMAP:
            return bm_iter(self.data)
        return (v for lo, hi in self.data for v in range(lo, hi + 1))

    def __contains__(self, v):
        if not 0 <= v < self.universe:
            return False
        if self.kind == self.BITMAP:
            return (self.data >> v) & 1 == 1
        i = bisect_right(self.data, (v, self.universe)) - 1
        return i >= 0 and self.data[i][1] >= v

    def to_list(self):
        return list(self)

    def __eq__(self, other):
        return (isinstance(other, BitSet)
                and self.universe == other.universe
                and self.count() == other.count()
                and self.issubset(other))

    def __repr__(self):
        return "BitSet(U=%d, kind=%s, count=%d)" % (self.universe, self.kind, self.count())

    # ---- 上界变化 ----

    def set_universe(self, new_upper):
        """调整上界。扩界对两种表示都免费（位图高位补零、区间与 U 无关）；
        缩界需要掩掉 >= new_upper 的位 / 裁剪越界区间，并重新决策表示。"""
        if new_upper < 0:
            raise ValueError("上界不能为负")
        if new_upper >= self.universe:
            return BitSet(new_upper, self.kind, self.data)
        if self.kind == self.BITMAP:
            bits = self.data & ((1 << new_upper) - 1)
            return self._from_bitmap_decided(bits, new_upper)
        ivs = []
        for lo, hi in self.data:
            if lo >= new_upper:
                break
            ivs.append((lo, min(hi, new_upper - 1)))
        return self._from_intervals_decided(ivs, new_upper)

    # ---- 不变量 ----

    def assert_invariants(self):
        assert self.universe >= 0
        assert self.kind in (self.BITMAP, self.INTERVAL)
        if self.kind == self.BITMAP:
            assert self.data >= 0, "位图不能为负"
            assert self.data >> self.universe == 0, "位图存在越界位"
        else:
            assert_normalized(self.data)
            for lo, hi in self.data:
                assert 0 <= lo and hi < self.universe, "区间越界"
