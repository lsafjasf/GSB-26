"""深度优先遍历的【修复版】实现（仅依赖标准库，Python 3.8+）。

修复点（对应缺陷版的四类问题）：
1. 显式栈迭代，不依赖递归深度，十万/百万级链式图不溢出。
2. 节点入栈前即标记 visited，每个节点恰好访问一次（环/菱形均安全）。
3. 并行边按 (u, v) 去重，同一条边在一次遍历中只处理一次。
4. 遍历状态为局部状态，且实例级状态在 try/finally 中保证复位，
   异常路径不残留，异常后再次遍历结果与首次完全一致。

遍历顺序规则（确定性、可复现）：
- 节点顺序：DFS 前序（preorder），与递归版顺序逐一相同。
- 邻接顺序：按 add_edge 的插入顺序展开邻居。
- 并行边：首次出现时处理，后续重复边跳过（不计数、不展开）。
"""


class Graph:
    """邻接表图。邻接表保持插入顺序，允许存储并行边（遍历时去重）。"""

    def __init__(self):
        self.adj = {}  # node -> [neighbor, ...]，dict 保持插入序

    def add_edge(self, u, v):
        self.adj.setdefault(u, []).append(v)
        self.adj.setdefault(v, [])

    def nodes(self):
        return list(self.adj.keys())

    def node_count(self):
        return len(self.adj)

    def unique_edge_count(self):
        """有向唯一边数（按 (u, v) 去重）。"""
        return sum(len(set(neighbors)) for neighbors in self.adj.values())


class DFS:
    """修复版 DFS。实例可复用：每次遍历结束后状态保证复位。"""

    def __init__(self, graph):
        self.graph = graph
        # 实例级状态仅用于遍历期间的 introspection，结束时必复位。
        self.visited = set()
        self.visit_order = []
        self.edges_processed = 0

    def traverse(self, start):
        """从 start 做 DFS，返回节点访问顺序列表。

        不变量：
        - 每个可达节点恰好访问一次（visit_order 无重复）。
        - 每条唯一有向边 (u, v) 恰好处理一次（edges_processed 计数）。
        - 抛异常时实例状态照样复位，再次遍历结果不变。
        """
        visited = set()
        order = []
        edges = 0
        # 同步到实例级，便于遍历期间外部观察；finally 中保证清理。
        self.visited = visited
        self.visit_order = order
        try:
            visited.add(start)
            order.append(start)
            # 栈元素：(节点, 邻居迭代器, 该节点已处理邻居集合)。
            # 迭代器保证与递归版相同的前序；去重集合随栈帧保存，
            # 子节点返回后继续去重，内存 O(当前路径出度之和)。
            stack = [(start, iter(self.graph.adj.get(start, ())), set())]
            while stack:
                u, it, seen_local = stack[-1]
                advanced = False
                for v in it:
                    if v in seen_local:
                        continue  # 并行边：同一条 (u, v) 只处理一次
                    seen_local.add(v)
                    edges += 1
                    if v not in visited:
                        visited.add(v)
                        order.append(v)
                        stack.append((v, iter(self.graph.adj.get(v, ())), set()))
                        advanced = True
                        break
                if not advanced:
                    stack.pop()
            self.edges_processed = edges
            return list(order)
        finally:
            # 缺陷4修复：无论正常返回还是异常，实例状态一律复位。
            self.visited = set()
            self.visit_order = []
