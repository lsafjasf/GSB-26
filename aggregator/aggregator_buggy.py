"""存在缺陷的多线程聚合统计模块（仅用于复现问题，勿用于生产）。

四类已知缺陷：
1. 并发写入：批次去重是“先检查后添加”的非原子操作，并发投递同一批次会被计入多次。
2. 批次重试：样本逐条计入、批次标识最后才登记，中途失败后重试会把已计入样本再计一次。
3. 读写并发：snapshot 直接读取正在原地修改的 dict/计数器，可看到部分更新的中间态。
4. 中途失败：校验与写入交织，失败时前面的样本已计入但批次未上报，状态不可回滚。
"""


class BuggyAggregator:
    def __init__(self):
        self.counts = {}          # dim -> int，原地修改，无锁
        self.total = 0            # 样本总数，与 counts 分开更新，无锁
        self._seen = set()        # 已处理批次标识
        # 测试钩子：在关键竞态窗口处调用，默认空操作。
        # 复现测试用它把竞态窗口确定性地撑开；基准测试保持默认以保证公平。
        self.yield_hook = lambda: None

    def apply_batch(self, batch_id, samples):
        """samples: [(dim, value), ...]。返回 True 表示计入，False 表示重复批次。"""
        # 缺陷1：check-then-act 无锁，两个线程可同时通过检查
        if batch_id in self._seen:
            return False
        self.yield_hook()  # 竞态窗口：两个线程都判定“未见过”
        for dim, value in samples:
            # 缺陷4：边校验边写入，中途失败时前面的样本已计入
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("invalid sample value: %r" % (value,))
            self.counts[dim] = self.counts.get(dim, 0) + value
            self.yield_hook()  # 缺陷3：counts 已更新、total 未更新的中间态
            self.total += value
        # 缺陷2：批次标识最后才登记；上面一旦抛异常，重试会重复计入
        self._seen.add(batch_id)
        return True

    def snapshot(self):
        """返回 (counts 副本, total)。缺陷3：无锁读取活跃状态。"""
        return dict(self.counts), self.total
