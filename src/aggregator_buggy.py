"""有缺陷的多线程聚合统计实现。

仅用于复现现网四类问题，请勿用于生产：
  1. 并发写入时部分样本被计入两次（样本级去重为无锁 check-then-act）
  2. 重试同一批样本导致计数翻倍（无批次级幂等）
  3. 统计输出读到部分更新的中间态（无锁遍历 + 计数分多步更新）
  4. 失败批次的部分样本已计入但未上报（逐条落账，无原子性）
"""

import threading
import time


class BuggyAggregator:
    def __init__(self, yield_on_write=True):
        self._counts = {}            # dim -> 计数
        self.total = 0               # 与 _counts 分步更新，存在中间态
        self._seen_samples = set()   # 样本级去重（无锁 check-then-act）
        # yield_on_write: 测试仪表，主动让出 GIL 以稳定复现竞态；
        # 吞吐基准测试时关闭，模拟“真实”裸写性能。
        self._yield = yield_on_write

    def _pause(self):
        if self._yield:
            time.sleep(0)

    def _add(self, dim, n):
        cur = self._counts.get(dim, 0)
        self._counts[dim] = cur + n
        t = self.total
        self.total = t + n

    def add_sample(self, sample_id, dim, n=1):
        # 缺陷1：check-then-act 无锁，并发下同一样本可通过多次检查
        if sample_id in self._seen_samples:
            return False
        self._pause()
        self._seen_samples.add(sample_id)
        self._add(dim, n)
        return True

    def apply_batch(self, batch_id, samples):
        # 缺陷2：batch_id 完全被忽略，重试即重复累加
        #        （样本级去重只挂在单条实时通路上，批量通路未做去重）
        # 缺陷4：逐条落账，中途抛错时前面的样本已计入
        applied = 0
        for sid, dim, n in samples:
            if n < 0:
                raise ValueError("negative sample value")
            self._add(dim, n)
            applied += 1
        return applied

    def snapshot(self):
        # 缺陷3：无锁遍历，写线程并发修改时读到中间态甚至抛 RuntimeError
        dims = {}
        for k, v in self._counts.items():
            self._pause()
            dims[k] = v
        return {"dims": dims, "total": self.total}
