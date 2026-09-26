"""修复后的多线程聚合统计实现。

设计要点（对应现网四类问题）：
  1. 并发写入：所有共享状态由同一把锁保护，不存在无锁 check-then-act。
  2. 批次重试：以调用方提供的 batch_id 作为幂等键去重，重复投递直接忽略。
  3. 一致快照：snapshot()/flush() 在锁内一次性拷贝，读者只能看到某个
     完整落账点之后的状态，绝不暴露批次中间态。
  4. 批次原子性：先整体校验、再在临界区内一次性落账；校验失败不记录
     batch_id、不产生任何部分计数，修正后可用同一 batch_id 安全重投。

去重依据：batch_id（建议上游使用“消息ID / 任务ID+批次序号”等全局唯一
标识）。去重表容量有界（dedup_capacity），超出后按 FIFO 驱逐最老记录；
调用方需保证重试发生在该窗口内。
"""

import threading
from collections import deque


class Aggregator:
    def __init__(self, dedup_capacity=1_000_000):
        self._lock = threading.Lock()
        self._dims = {}             # dim -> 当前窗口计数
        self._total = 0             # 当前窗口总数
        self._cum_dims = {}         # dim -> 累计计数（单调不减）
        self._cum_total = 0         # 累计总数（单调不减）
        self._seen_batches = set()  # 批次幂等键
        self._batch_order = deque()
        self._dedup_capacity = dedup_capacity

    @staticmethod
    def _validate(samples):
        prepared = []
        for dim, n in samples:
            if not isinstance(dim, str) or not dim:
                raise ValueError(f"invalid dimension: {dim!r}")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise ValueError(f"invalid sample value: {n!r}")
            prepared.append((dim, n))
        return prepared

    def apply_batch(self, batch_id, samples):
        """应用一个批次。返回 True 表示已落账，False 表示重复投递被忽略。

        批次中途校验失败抛 ValueError，此时不落任何计数、不记录幂等键。
        """
        prepared = self._validate(samples)  # 先整体校验，失败则零副作用
        batch_sum = sum(n for _, n in prepared)
        with self._lock:
            if batch_id in self._seen_batches:
                return False
            self._seen_batches.add(batch_id)
            self._batch_order.append(batch_id)
            while len(self._batch_order) > self._dedup_capacity:
                self._seen_batches.discard(self._batch_order.popleft())
            for dim, n in prepared:
                self._dims[dim] = self._dims.get(dim, 0) + n
                self._cum_dims[dim] = self._cum_dims.get(dim, 0) + n
            self._total += batch_sum
            self._cum_total += batch_sum
        return True

    def snapshot(self):
        """返回某一一致状态的全量快照（窗口值 + 累计值）。"""
        with self._lock:
            return {
                "dims": dict(self._dims),
                "total": self._total,
                "cum_dims": dict(self._cum_dims),
                "cum_total": self._cum_total,
            }

    def flush(self):
        """原子地取出当前窗口并清零；累计值不回退，保证单调性。"""
        with self._lock:
            window = {"dims": self._dims, "total": self._total}
            self._dims = {}
            self._total = 0
            return window
