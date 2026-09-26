"""修复后的多线程聚合统计模块（仅标准库）。

修复要点：
1. 并发写入：所有共享状态由同一把互斥锁保护；批次去重的
   “检查 + 计入 + 登记”在同一临界区内完成，并发投递同一批次只生效一次。
2. 批次重试（幂等）：以调用方提供的 batch_id 作为去重依据，
   成功提交后登记到 _applied_batches；重复投递返回 False，不产生任何效果
   （at-least-once 投递 -> exactly-once 效果）。
3. 一致读：snapshot 在锁内整体拷贝，读到的必然是某次批次提交后的完整状态，
   不会看到批次内部的中间态；计数只增不减，快照间总量单调不回退。
4. 中途失败：先在临界区外完成整批校验与预聚合，校验不过则整批拒绝、
   状态零改动（all-or-nothing）；进入临界区后的操作不会再失败。

生产化备注：_applied_batches 无界增长，实际部署可替换为带 TTL/LRU 的
去重结构（如 OrderedDict 滑动窗口或外部 KV），去重键仍用 batch_id。

吞吐说明：修复的代价是每批一次锁获取（临界区仅若干次 dict 写入，微秒级）。
曾评估过分条（stripe）锁方案，但在 CPython GIL 下每批多次锁获取/释放的
竞争开销反而更高，实测全面慢于单锁，故保留单锁实现。
"""

import threading


class Aggregator:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}            # dim -> int，仅在锁内修改
        self._total = 0              # 已提交样本总数，仅在锁内修改
        self._applied_batches = set()  # 已提交批次标识（幂等去重依据）

    @staticmethod
    def _validate_and_preaggregate(samples):
        """临界区外完成全部校验与预聚合；任何样本非法则整批拒绝。"""
        deltas = {}
        for dim, value in samples:
            if not isinstance(dim, str) or not dim:
                raise ValueError("invalid dimension: %r" % (dim,))
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("invalid sample value: %r" % (value,))
            deltas[dim] = deltas.get(dim, 0) + value
        return deltas

    def apply_batch(self, batch_id, samples):
        """原子地计入一个批次。

        返回 True 表示本次计入生效；返回 False 表示重复批次（幂等忽略）。
        校验失败抛 ValueError，且状态零改动。
        """
        if batch_id is None:
            raise ValueError("batch_id is required for exactly-once semantics")
        deltas = self._validate_and_preaggregate(samples)
        with self._lock:
            if batch_id in self._applied_batches:
                return False
            for dim, delta in deltas.items():
                self._counts[dim] = self._counts.get(dim, 0) + delta
                self._total += delta
            self._applied_batches.add(batch_id)
            return True

    def snapshot(self):
        """返回 (counts 副本, total)，是某个一致状态：sum(counts) == total。"""
        with self._lock:
            return dict(self._counts), self._total

    def total(self):
        with self._lock:
            return self._total
