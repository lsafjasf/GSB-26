"""缺陷版分片计数器（仅用于复现现网四类问题，请勿用于生产）。

四类缺陷：
1. increment 为非原子的 read-modify-write 且无锁 -> 高并发下丢失更新，总数偏少。
2. merge 先清分片、后累加 base，中间状态对 reader 可见 -> 读取出现回退；
   两个 merge 并发时分片被减成负数 -> 读取出现负数。
3. merge 的"取值/清零/累加"三步不原子：并发 increment 被清零吞掉（丢失），
   并发 merge 会把同一个分片值重复累加进 base（重复计数）。
4. 分片下标 = hash(key) % N，热点键把所有线程压到同一分片，冲突与丢失最严重。
"""

import threading
import time


class BuggyShardedCounter:
    def __init__(self, shards=16, yield_on_write=True):
        self.n = shards
        self.shards = [0] * shards
        self.base = 0
        # yield_on_write 在关键竞态窗口中主动让出 GIL，
        # 模拟非 GIL 语言 / 真实调度下的交错，使缺陷稳定复现。
        self._yield = yield_on_write

    def _idx(self, key):
        # 缺陷 4：只按 key 哈希，同一热点键的所有线程全部落在同一分片。
        return hash(key) % self.n

    def increment(self, key, delta=1):
        i = self._idx(key)
        # 缺陷 1：无锁 read-modify-write，并发下互相覆盖。
        v = self.shards[i]
        if self._yield:
            time.sleep(0)
        self.shards[i] = v + delta

    def read(self):
        # 缺陷 2：读取不加锁，merge 的中间态（分片已清、base 未加、
        # 或分片被重复减成负数）会被读到 -> 回退或负数。
        return self.base + sum(self.shards)

    def merge(self):
        # 缺陷 3：取值 / 清零 / 累加 base 三步不原子。
        for i in range(self.n):
            v = self.shards[i]
            if self._yield:
                time.sleep(0)
            self.shards[i] -= v  # 并发 increment 在此窗口被吞掉；并发 merge 减成负数
            if self._yield:
                time.sleep(0)
            self.base += v  # 并发 merge 把同一 v 重复累加 -> 重复计数
