"""深度优先遍历的【缺陷版】实现（仅用于复现现网四类问题）。

四类已知缺陷：
1. 递归实现，超深链式图触发 RecursionError（栈溢出）。
2. 起始节点从未加入 visited，存在环时某些节点被重复访问。
3. 邻接表中的并行边（重边）不做去重，同一条边被处理多次。
4. visited 是实例级状态且异常路径不清理，后续遍历结果被污染。
"""


class BuggyGraph:
    """邻接表图，按插入顺序保存邻接边（允许并行边）。"""

    def __init__(self):
        self.adj = {}  # node -> [neighbor, ...]

    def add_edge(self, u, v):
        self.adj.setdefault(u, []).append(v)
        self.adj.setdefault(v, [])

    def nodes(self):
        return list(self.adj.keys())


class BuggyDFS:
    """缺陷版 DFS：递归 + 实例级 visited + 起始节点漏标记 + 边不去重。"""

    def __init__(self, graph):
        self.graph = graph
        self.visited = set()      # 缺陷4：实例级状态，遍历间不重置
        self.visit_order = []     # 节点访问顺序（含重复）
        self.edges_processed = 0  # 缺陷3：每遇到一条邻接边就计数，不去重

    def traverse(self, start):
        self._visit(start)
        return self.visit_order

    def _visit(self, u):
        # 缺陷2：只把“子节点”加入 visited，u 自身从不加入。
        self.visit_order.append(u)
        for v in self.graph.adj.get(u, ()):
            self.edges_processed += 1
            if v not in self.visited:
                self.visited.add(v)
                self._visit(v)  # 缺陷1：递归，深图栈溢出
