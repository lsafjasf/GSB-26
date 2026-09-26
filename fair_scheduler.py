"""多队列加权公平调度器（Deficit Round Robin, DRR）。

特性：
- 多队列按权重出队，队列内部严格 FIFO；
- 空队列不消耗配额（deficit 清零），配额在活跃队列间按权重重新分配；
- 权重为 0 的队列永不调度（但不报错、不影响其他队列）；
- 时间源可注入（clock 参数），便于测试；
- 公平性上界：固定活跃集合下，|count_i - T*w_i/W| <= w_i*Q*(1 - w_i/W)；
- 饥饿上界：任一非空队列 i 在任意 (W - w_i)*Q + 1 次出队内必被调度。
"""

from collections import deque
import time


class QueueExistsError(ValueError):
    pass


class NoSuchQueueError(KeyError):
    pass


class _QueueState:
    __slots__ = ("name", "weight", "items", "deficit", "served")

    def __init__(self, name, weight):
        self.name = name
        self.weight = weight
        self.items = deque()
        self.deficit = 0
        self.served = 0


class WeightedFairScheduler:
    """加权公平调度器。

    参数：
        quantum: 每轮每单位权重获得的配额基数（默认 1，任务代价为 1）。
        clock:   可注入的时间源，返回单调时间戳（秒），用于统计。
    """

    def __init__(self, quantum=1, clock=time.monotonic):
        if not isinstance(quantum, int) or quantum < 1:
            raise ValueError("quantum 必须为正整数")
        self._quantum = quantum
        self._clock = clock
        self._queues = {}          # name -> _QueueState
        self._order = []           # 固定轮转顺序（队列名）
        self._cursor = 0           # 当前轮转游标
        self._pending = 0          # 所有队列中待处理任务总数
        self._total_served = 0
        self._created_at = clock()
        self._last_served_at = None

    # ------------------------------------------------------------------ API

    def add_queue(self, name, weight):
        """注册队列。weight 必须为非负整数；0 表示永不调度。"""
        if name in self._queues:
            raise QueueExistsError(f"队列已存在: {name!r}")
        if not isinstance(weight, int) or weight < 0:
            raise ValueError("weight 必须为非负整数")
        self._queues[name] = _QueueState(name, weight)
        self._order.append(name)

    def set_weight(self, name, weight):
        """运行时调整权重（可配置）。"""
        if not isinstance(weight, int) or weight < 0:
            raise ValueError("weight 必须为非负整数")
        self._get(name).weight = weight

    def enqueue(self, name, item):
        q = self._get(name)
        q.items.append(item)
        self._pending += 1

    def dequeue(self):
        """按加权公平规则取出一个任务；无任务时返回 None。

        DRR 单步语义：游标指向的队列若 deficit 不足则补充
        weight*quantum 配额；配额足够且队列非空则出队一个任务。
        空队列 deficit 清零，不累积配额。
        """
        if self._pending == 0:
            return None
        n = len(self._order)
        for _ in range(n):  # 最多扫描一整圈，保证终止
            q = self._queues[self._order[self._cursor]]
            if not q.items or q.weight == 0:
                # 空队列 / 零权队列：不累积配额，直接跳过
                q.deficit = 0
                self._cursor = (self._cursor + 1) % n
                continue
            if q.deficit < 1:
                q.deficit += q.weight * self._quantum
            if q.deficit >= 1:
                item = q.items.popleft()
                q.deficit -= 1
                q.served += 1
                self._pending -= 1
                self._total_served += 1
                self._last_served_at = self._clock()
                if q.deficit < 1 or not q.items:
                    if not q.items:
                        q.deficit = 0
                    self._cursor = (self._cursor + 1) % n
                return item
            self._cursor = (self._cursor + 1) % n
        # 所有非空队列权重均为 0
        return None

    # -------------------------------------------------------------- 统计

    @property
    def pending(self):
        return self._pending

    @property
    def total_served(self):
        return self._total_served

    def served_count(self, name):
        return self._get(name).served

    def stats(self):
        return {
            "created_at": self._created_at,
            "last_served_at": self._last_served_at,
            "total_served": self._total_served,
            "pending": self._pending,
            "per_queue": {name: q.served for name, q in self._queues.items()},
        }

    # -------------------------------------------------------------- 内部

    def _get(self, name):
        try:
            return self._queues[name]
        except KeyError:
            raise NoSuchQueueError(name) from None
