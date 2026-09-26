"""Space-Saving 流式频繁项近似统计（仅标准库）。

算法：Metwally, Agrawal, El Abbadi, "Efficient Computation of Frequent
and Top-k Elements in Data Streams" (ICDT 2005)。

误差界（核心性质）：
    设容量为 m 个计数器，流中元素总权重为 N，则对任意键 x：
        exact(x) <= est(x) <= exact(x) + N / m      （x 被监控时）
        est(x) = 0 且 exact(x) <= N / m             （x 未被监控时）
    即对任意键：|est(x) - exact(x)| <= N / m。
    误差界只与内存参数 m 和流总量 N 有关，与数据分布无关。

合并：
    两个容量分别为 m1、m2 的摘要（流总量 N1、N2）合并后取容量
    m = min(m1, m2)，误差界为 (N1 + N2) / m，可递归推广到多路合并。
"""

import heapq

__all__ = ["SpaceSaving"]


class SpaceSaving:
    """固定内存预算的近似频次统计结构。"""

    __slots__ = ("capacity", "counts", "n", "_heap", "_seq")

    def __init__(self, capacity):
        """capacity: 计数器个数 m，决定内存占用与误差界 N/m。"""
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self.counts = {}   # key -> 估计计数（仅保存被监控的键，<= capacity 个）
        self.n = 0         # 流中元素总权重 N
        self._heap = []    # (count, seq, key) 惰性最小堆，用于 O(log m) 找最小计数器
        self._seq = 0

    # ------------------------------------------------------------------ #
    # 更新
    # ------------------------------------------------------------------ #
    def update(self, key, weight=1):
        """处理一个流元素（可带正整数权重）。"""
        if weight <= 0:
            raise ValueError("weight must be positive")
        self.n += weight
        current = self.counts.get(key)
        if current is not None:
            current += weight
        elif len(self.counts) < self.capacity:
            current = weight
        else:
            # 用最小计数器替换：新键估计值 = 最小计数 + weight
            min_count, min_key = self._pop_min()
            del self.counts[min_key]
            current = min_count + weight
        self.counts[key] = current
        self._push(current, key)
        if len(self._heap) > 2 * self.capacity:
            self._rebuild_heap()

    def _push(self, count, key):
        self._seq += 1
        heapq.heappush(self._heap, (count, self._seq, key))

    def _pop_min(self):
        heap = self._heap
        counts = self.counts
        while heap:
            count, _, key = heapq.heappop(heap)
            if counts.get(key) == count:  # 跳过过期堆项
                return count, key
        raise RuntimeError("heap out of sync with counters")  # 不应发生

    def _rebuild_heap(self):
        self._heap = [(c, i, k) for i, (k, c) in enumerate(self.counts.items())]
        heapq.heapify(self._heap)
        self._seq = len(self._heap)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def query(self, key):
        """返回键的近似计数；未被监控的键返回 0（其真实计数 <= error_bound）。"""
        return self.counts.get(key, 0)

    def error_bound(self):
        """当前误差界：任意键 |est - exact| <= N / m。"""
        return self.n / self.capacity

    def topk(self, k, include_bound=False):
        """返回估计计数最高的 k 个 (key, est)，按估计值降序。

        保证：任何真实计数 > error_bound 的键一定出现在摘要中，
        因此当 k 不超过真实计数 > error_bound 的键数时，topk 是精确的集合。
        """
        if k < 0:
            raise ValueError("k must be >= 0")
        items = heapq.nlargest(k, self.counts.items(), key=lambda kv: kv[1])
        if include_bound:
            bound = self.error_bound()
            return [(key, est, bound) for key, est in items]
        return items

    def __len__(self):
        return len(self.counts)

    def __contains__(self, key):
        return key in self.counts

    # ------------------------------------------------------------------ #
    # 合并
    # ------------------------------------------------------------------ #
    def merge(self, other):
        """合并两个摘要，返回新结构（不修改原结构）。

        结果容量 m = min(self.capacity, other.capacity)，
        结果误差界 = (N1 + N2) / m。

        推导：合并前对任意键有 est_i <= exact_i + N_i/m_i，故逐项相加后
        误差 <= N1/m1 + N2/m2 的部分被容量截断吸收——被丢弃的计数器
        不超过第 m 大值，而计数器总和 <= N1+N2，所以第 m 大值
        <= (N1+N2)/m；对被保留键，未出现在某一侧摘要中的部分其真实
        计数 <= 该侧误差界。综上 |est - exact| <= (N1+N2)/m。
        """
        if not isinstance(other, SpaceSaving):
            raise TypeError("can only merge with another SpaceSaving")
        capacity = min(self.capacity, other.capacity)
        merged = dict(self.counts)
        for key, value in other.counts.items():
            merged[key] = merged.get(key, 0) + value
        if len(merged) > capacity:
            merged = dict(heapq.nlargest(capacity, merged.items(), key=lambda kv: kv[1]))
        result = SpaceSaving(capacity)
        result.counts = merged
        result.n = self.n + other.n
        result._rebuild_heap()
        return result

    @classmethod
    def merge_all(cls, summaries):
        """多路合并；误差界 = 所有分片 N 之和 / min(各分片容量)。"""
        summaries = list(summaries)
        if not summaries:
            raise ValueError("nothing to merge")
        result = summaries[0]
        for s in summaries[1:]:
            result = result.merge(s)
        return result
