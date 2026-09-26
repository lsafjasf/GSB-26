"""example.py — 库用法示例：求最大流并输出最小割边集合。"""

from maxflow import MaxFlow

# CLRS 经典网络
mf = MaxFlow(6)
for u, v, c in [(0, 1, 16), (0, 2, 13), (1, 2, 10), (2, 1, 4),
                (1, 3, 12), (3, 2, 9), (2, 4, 14), (4, 3, 7),
                (3, 5, 20), (4, 5, 4)]:
    mf.add_edge(u, v, c)

value = mf.max_flow(0, 5)
cut_value, cut_edges, S = mf.min_cut(0, 5)

print(f"max flow      = {value}")
print(f"min cut value = {cut_value}")
print(f"S side        = {[i for i in range(6) if S[i]]}")
print(f"cut edges     = {cut_edges}")
print(f"flow edges    = {mf.flow_edges()}")
assert value == cut_value == sum(c for _, _, c in cut_edges)
