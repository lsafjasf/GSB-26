"""支持回滚的并查集（Rollback Disjoint Set Union）。

设计要点：
- 按大小合并（union by size）：小树挂到大树下，树高 O(log n)。
- 不使用路径压缩：路径压缩会修改大量节点的父指针，
  回滚时无法以 O(1) 均摊代价撤销，因此禁用。
- 每次成功合并向操作日志压入一条撤销记录，
  回滚即逐条弹出并逆操作，天然支持任意检查点回滚。

复杂度（n 为元素个数）：
- find(x)      : O(log n)   —— 树高被按大小合并限制在 log n
- union(a, b)  : O(log n)   —— 两次 find + O(1) 修改
- checkpoint() : O(1)       —— 返回当前日志长度
- rollback(cp) : O(k)       —— k 为检查点之后发生的成功合并次数
- 空间         : O(n + m)   —— m 为当前未回滚的合并次数（日志深度）
"""

from typing import List, Tuple


class RollbackDSU:
    __slots__ = ("_parent", "_size", "_log", "_components")

    def __init__(self, n: int = 0):
        if n < 0:
            raise ValueError("n must be non-negative")
        self._parent: List[int] = list(range(n))
        self._size: List[int] = [1] * n
        # 每条日志: (被挂根 child, 新根 root, 合并前 root 的 size)
        self._log: List[Tuple[int, int, int]] = []
        self._components: int = n

    # ---------- 基本操作 ----------

    def find(self, x: int) -> int:
        """返回 x 所在集合的根。不做路径压缩。"""
        parent = self._parent
        while parent[x] != x:
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        """合并 a、b 所在集合；已在同一集合则返回 False（不写日志）。"""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        # 按大小合并：小根挂到大根下
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._log.append((rb, ra, self._size[ra]))
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        self._components -= 1
        return True

    def connected(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)

    def component_size(self, x: int) -> int:
        return self._size[self.find(x)]

    @property
    def num_components(self) -> int:
        return self._components

    def __len__(self) -> int:
        return len(self._parent)

    # ---------- 回滚 ----------

    def checkpoint(self) -> int:
        """返回一个检查点句柄（当前日志深度）。"""
        return len(self._log)

    def rollback(self, cp: int = 0) -> None:
        """撤销检查点 cp 之后的全部合并。cp 必须 <= 当前日志深度。"""
        if cp < 0 or cp > len(self._log):
            raise ValueError(f"invalid checkpoint {cp}, log depth {len(self._log)}")
        log = self._log
        while len(log) > cp:
            child, root, old_size = log.pop()
            self._parent[child] = child
            self._size[root] = old_size
            self._components += 1

    # ---------- 辅助（测试/对拍用） ----------

    def partition(self):
        """返回当前集合划分：{根: 有序成员列表}，根与成员均排序，便于比较。"""
        groups = {}
        for i in range(len(self._parent)):
            groups.setdefault(self.find(i), []).append(i)
        return {root: members for root, members in sorted(groups.items())}
