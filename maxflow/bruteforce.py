"""bruteforce.py — 暴力增广参考实现（Ford-Fulkerson，逐条 DFS 增广）

仅用于对拍验证正确性，不追求效率。
复杂度 O(E * maxflow)，小图上可用。
"""

from maxflow import MaxFlow  # 复用同一数据结构，增广策略不同


class BruteForceMaxFlow(MaxFlow):
    def max_flow(self, s, t):
        if not (0 <= s < self.n and 0 <= t < self.n):
            raise ValueError("vertex out of range")
        if s == t:
            return 0
        flow = 0
        while True:
            # 每次找一条增广路，按 bottleneck 增广
            parent = [None] * self.n   # (前驱点, 边对象)
            seen = [False] * self.n
            seen[s] = True
            stack = [s]
            while stack and not seen[t]:
                u = stack.pop()
                for e in self.g[u]:
                    if e.cap > 0 and not seen[e.to]:
                        seen[e.to] = True
                        parent[e.to] = (u, e)
                        stack.append(e.to)
            if not seen[t]:
                return flow
            # bottleneck
            bott = None
            v = t
            while v != s:
                u, e = parent[v]
                bott = e.cap if bott is None else min(bott, e.cap)
                v = u
            v = t
            while v != s:
                u, e = parent[v]
                e.cap -= bott
                self.g[e.to][e.rev].cap += bott
                v = u
            flow += bott
