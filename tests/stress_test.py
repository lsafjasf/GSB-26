"""对拍：RollbackDSU vs 朴素重建实现。

朴素参考实现：只记录"生效的合并序列"，每次查询时从头重放全部合并
重建划分（允许路径压缩，反正是参考实现）。任何时刻两侧的集合划分
必须完全一致。

用法: python3 tests/stress_test.py [轮数] [种子]
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from rollback_dsu import RollbackDSU


class NaiveRebuildDSU:
    """朴素重建：保存生效合并历史，查询时全量重放。"""

    def __init__(self, n):
        self.n = n
        self.history = []  # 生效的 (a, b) 合并

    def union(self, a, b):
        if self._find(a) != self._find(b):
            self.history.append((a, b))
            return True
        return False

    def checkpoint(self):
        return len(self.history)

    def rollback(self, cp):
        del self.history[cp:]

    def _rebuild(self):
        parent = list(range(self.n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self.history:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        return parent, find

    def _find(self, x):
        parent, find = self._rebuild()
        return find(x)

    def partition(self):
        parent, find = self._rebuild()
        groups = {}
        for i in range(self.n):
            groups.setdefault(find(i), []).append(i)
        return {r: m for r, m in sorted(groups.items())}


def canon(part):
    """划分的规范形式：排序后的成员元组的排序元组，与根标签无关。"""
    return tuple(sorted(tuple(m) for m in part.values()))


def run_round(n, ops, seed):
    rng = random.Random(seed)
    fast = RollbackDSU(n)
    slow = NaiveRebuildDSU(n)
    cps_fast, cps_slow = [], []
    for step in range(ops):
        action = rng.random()
        if action < 0.45:                       # 随机合并
            a, b = rng.randrange(n), rng.randrange(n)
            r1, r2 = fast.union(a, b), slow.union(a, b)
            assert r1 == r2, f"union 返回值不一致 step={step}"
        elif action < 0.60:                     # 打检查点
            cps_fast.append(fast.checkpoint())
            cps_slow.append(slow.checkpoint())
        elif action < 0.80 and cps_fast:        # 回滚到随机历史检查点
            i = rng.randrange(len(cps_fast))
            fast.rollback(cps_fast[i])
            slow.rollback(cps_slow[i])
            del cps_fast[i + 1:]                # 检查点之后的检查点语义上失效
            del cps_slow[i + 1:]
        # 每步全量比对划分
        assert canon(fast.partition()) == canon(slow.partition()), (
            f"划分不一致: n={n} step={step} seed={seed}")
        assert fast.num_components == len(slow.partition())


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    base_seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260926
    for r in range(rounds):
        rng = random.Random(base_seed + r)
        n = rng.choice([1, 2, 3, 7, 16, 50, 200])
        ops = rng.choice([50, 200, 1000])
        run_round(n, ops, base_seed + r)
        if (r + 1) % 50 == 0:
            print(f"  ... {r + 1}/{rounds} 轮通过")
    print(f"对拍通过：{rounds} 轮随机合并/回滚，两侧集合划分完全一致")


if __name__ == "__main__":
    main()
