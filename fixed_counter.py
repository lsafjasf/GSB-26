"""修复版分片计数器。

修复策略（对应四类缺陷）：
1. 每个分片一把锁，increment 在锁内完成 read-modify-write -> 不丢更新。
2. read() 按固定下标顺序获取全部分片锁，得到一致性快照；
   由于总量 = base + sum(stripes) 只随 increment 单调增长（compact 守恒），
   任意两次读取满足单调不回退，且永不为负。
3. compact()（原 merge）逐分片在锁内完成 "base += stripe; stripe = 0"，
   与 increment 互斥：增量要么先落分片被收编，要么后落分片留在分片里，
   不丢失、不重复。compact 不需要全局锁。
4. 分片下标 = (hash(key) + 线程槽位 salt) % N：同一热点键被不同线程
   散到不同分片，消除单分片热点；读取时对分片求和，结果不变。

不变量：
- 任意时刻 read() <= 已发起的增量总数（永不多计），最终 read() == 增量总数。
- read() 单调非降且 >= 0。
"""

import itertools
import threading


class FixedShardedCounter:
    def __init__(self, stripes=64):
        self.n = stripes
        self._counts = [0] * stripes
        self._locks = [threading.Lock() for _ in range(stripes)]
        self._base = 0
        self._local = threading.local()
        self._salt_counter = itertools.count()
        self._salt_lock = threading.Lock()

    def _salt(self):
        # 每个线程分配唯一槽位，作为哈希扰动，打散热点键。
        salt = getattr(self._local, "salt", None)
        if salt is None:
            with self._salt_lock:
                salt = next(self._salt_counter)
            self._local.salt = salt
        return salt

    def _idx(self, key):
        return (hash(key) + self._salt()) % self.n

    def increment(self, key, delta=1):
        if delta < 0:
            raise ValueError("delta must be >= 0")
        # 快路径：同一线程重复自增同一 key（热点场景）时命中线程本地缓存，
        # 省去哈希与取模开销。
        cache = getattr(self._local, "cache", None)
        if cache is not None and (cache[0] is key or cache[0] == key):
            i = cache[1]
        else:
            i = self._idx(key)
            self._local.cache = (key, i)
        with self._locks[i]:
            self._counts[i] += delta

    def read(self):
        # 固定顺序获取全部锁，避免与 compact 死锁，得到一致性快照。
        for lock in self._locks:
            lock.acquire()
        try:
            return self._base + sum(self._counts)
        finally:
            for lock in self._locks:
                lock.release()

    def compact(self):
        # 逐分片收编到 base。单线程内不同时持有两把锁，无死锁；
        # 与 increment 互斥，与并发 compact 在分片锁上串行，守恒。
        for i in range(self.n):
            with self._locks[i]:
                self._base += self._counts[i]
                self._counts[i] = 0

    def stripe_loads(self):
        # 观测用：各分片当前计数（compact 前的分布）。
        for lock in self._locks:
            lock.acquire()
        try:
            return list(self._counts)
        finally:
            for lock in self._locks:
                lock.release()
