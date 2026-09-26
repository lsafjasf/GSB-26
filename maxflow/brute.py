"""暴力参考实现，仅供测试对拍使用（仅标准库）。

- brute_max_flow：递归 DFS 反复增广的 Ford-Fulkerson，
  小规模整数容量下与 Dinic 结果必须一致；
- brute_min_cut_capacity：枚举全部源汇分离割求最小割容量，
  独立验证最大流最小割定理（n <= ~20 时可用）。
"""

from collections import deque
from itertools import combinations


def brute_max_flow(n, edges, s, t):
    """邻接表 Ford-Fulkerson（整数容量）。返回最大流值。"""
    if s == t:
        return 0
    cap = [[0] * n for _ in range(n)]
    adj = [set() for _ in range(n)]
    for u, v, c in edges:
        if u == v:
            continue
        cap[u][v] += c
        adj[u].add(v)
        adj[v].add(u)

    total = 0

    def dfs(u, pushed, visited):
        if u == t:
            return pushed
        visited[u] = True
        for v in adj[u]:
            if not visited[v] and cap[u][v] > 0:
                bound = cap[u][v] if pushed is None else min(pushed, cap[u][v])
                got = dfs(v, bound, visited)
                if got > 0:
                    cap[u][v] -= got
                    cap[v][u] += got
                    return got
        return 0

    while True:
        got = dfs(s, None, [False] * n)
        if not got:
            return total
        total += got


def brute_min_cut_capacity(n, edges, s, t):
    """枚举所有满足 s in S、t not in S 的割，返回最小割容量。"""
    if s == t:
        return 0
    others = [i for i in range(n) if i not in (s, t)]
    best = None
    for r in range(len(others) + 1):
        for subset in combinations(others, r):
            side = set(subset)
            side.add(s)
            value = 0
            for u, v, c in edges:
                if u in side and v not in side:
                    value += c
            if best is None or value < best:
                best = value
    return best


def is_bfs_reachable(n, edges, s, t):
    """只看容量 > 0 的边，t 是否可达（供退化用例检查）。"""
    if s == t:
        return True
    adj = [[] for _ in range(n)]
    for u, v, c in edges:
        if c > 0:
            adj[u].append(v)
    seen = [False] * n
    seen[s] = True
    queue = deque([s])
    while queue:
        u = queue.popleft()
        for v in adj[u]:
            if not seen[v]:
                seen[v] = True
                queue.append(v)
    return seen[t]
