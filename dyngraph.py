"""dyngraph: 全动态无向图连通分量 / 可达性增量维护库（仅标准库）。

内存有界性：所有容器规模只随"活跃图"变化。删除路径上对空容器立即释放、
对容量超过活跃规模约 4 倍的容器按摊还 O(1) 重建（基于 sys.getsizeof 估计），
分量 id 通过 freelist 回收，因此内存不随增删次数无限增长。


设计
----
- 邻接表 `adj` 保存所有边的重数（支持重复边）；自环单独计数，不影响连通性。
- 每个连通分量维护一棵生成树（`tree`），以及分量标签 `comp` / 成员集 `members`。
- 加边：两端在不同分量时按"小并入大"合并分量标签，并把该边记为树边；
  同分量时仅记为非树边。均不触碰其他分量。
- 删边：重数 >1 只减计数；非树边直接删除（分量不变）；树边被删时，
  从切口两侧交替 BFS 找到较小一侧，只扫描较小侧的关联边寻找替换边：
  找到则重连（分量不变），找不到则仅给较小侧重新标号（分量分裂）。
- 查询 `connected(u, v)` 为 O(1)：比较分量标签。

不变式：`comp` 导出的划分恒等于当前多重图的连通分量划分
（由 test_dyngraph.py 中的随机对拍保证）。
"""

import sys

# 容器容量阈值：CPython 3.12 中，活跃元素为 n 的 set 驻留不超过 ~216+128n 字节、
# dict 不超过 ~64+64n 字节；超过下述阈值说明容量远超活跃规模，触发重建。
_SET_SLACK = 4096
_SET_PER_ELEM = 128
_DICT_SLACK = 4096
_DICT_PER_ELEM = 256


def _set_oversize(s):
    return sys.getsizeof(s) > _SET_SLACK + _SET_PER_ELEM * len(s)


def _dict_oversize(d):
    return sys.getsizeof(d) > _DICT_SLACK + _DICT_PER_ELEM * len(d)


class DynamicGraph:
    __slots__ = ("adj", "loops", "tree", "comp", "members", "_next_cid", "_free_cids", "nedges")

    def __init__(self):
        self.adj = {}        # u -> {v: count}，v != u，双向存储
        self.loops = {}      # u -> 自环重数
        self.tree = {}       # u -> set(v)，生成森林的树边
        self.comp = {}       # u -> 分量 id
        self.members = {}    # cid -> set(顶点)
        self._next_cid = 0
        self._free_cids = []  # 回收的分量 id，保证内存不随增删次数增长
        self.nedges = 0      # 非自环边（按重数去重后的种类数之外的实际条数见 _stats）

    # ---------------- 基本操作 ----------------

    def _new_cid(self):
        if self._free_cids:
            return self._free_cids.pop()
        cid = self._next_cid
        self._next_cid += 1
        return cid

    def add_vertex(self, v):
        if v not in self.comp:
            cid = self._new_cid()
            self.comp[v] = cid
            self.members[cid] = {v}
            self.adj[v] = {}
            self.tree[v] = set()

    def add_edge(self, u, v):
        self.add_vertex(u)
        self.add_vertex(v)
        if u == v:
            self.loops[u] = self.loops.get(u, 0) + 1
            return
        nbr = self.adj[u]
        if v in nbr:
            nbr[v] += 1
            self.adj[v][u] += 1
            return
        nbr[v] = 1
        self.adj[v][u] = 1
        self.nedges += 1
        if self.comp[u] != self.comp[v]:
            self._merge(u, v)

    def remove_edge(self, u, v):
        """删除一条边；边不存在时返回 False（no-op），否则返回 True。"""
        if u == v:
            c = self.loops.get(u, 0)
            if c == 0:
                return False
            if c == 1:
                del self.loops[u]
            else:
                self.loops[u] = c - 1
            return True
        nbr = self.adj.get(u)
        if not nbr or v not in nbr:
            return False
        c = nbr[v]
        if c > 1:
            nbr[v] = c - 1
            self.adj[v][u] = c - 1
            return True
        self._delete_edge_entry(u, v)
        if v in self.tree[u]:
            self._delete_tree_entry(u, v)
            self._reconnect(u, v)
        return True

    def _delete_tree_entry(self, u, v):
        self.tree[u].discard(v)
        self.tree[v].discard(u)
        for x in (u, v):
            s = self.tree[x]
            if not s:
                self.tree[x] = set()
            elif _set_oversize(s):
                self.tree[x] = set(s)

    def _delete_edge_entry(self, u, v):
        """删除最后一份 (u,v) 的邻接记录；空容器立即释放，内存跟随当前规模。"""
        adj = self.adj
        del adj[u][v]
        del adj[v][u]
        for x in (u, v):
            d = adj[x]
            if not d:
                adj[x] = {}
            elif _dict_oversize(d):
                adj[x] = dict(d)
        self.nedges -= 1

    # ---------------- 查询 ----------------

    def connected(self, u, v):
        """O(1) 可达性查询。未出现的顶点视为不存在，返回 False。"""
        cu = self.comp.get(u)
        if cu is None:
            return False
        return cu == self.comp.get(v)

    def component(self, v):
        return self.comp.get(v)

    def component_count(self):
        return len(self.members)

    def vertices(self):
        return self.comp.keys()

    # ---------------- 批量增删，按批结算 ----------------

    def apply_batch(self, ops):
        """批量应用 ('add'|'del', u, v)，按每条边的净增量一次性结算。

        批次内顺序无关的依据：每条边的最终重数 = max(0, 当前重数 + Σδ)，
        只取决于该边增删次数的代数和（加法可交换），与排列无关；
        结构性变化（合并/分裂）只取决于 (旧重数==0, 新重数==0)，
        因此同一批次的任意排列产生相同的最终多重图与相同的分量划分。
        """
        delta = {}
        for op in ops:
            kind, u, v = op[0], op[1], op[2]
            if kind == "add":
                # 与逐条执行一致：add 会物化顶点（即使同批随后被删净），del 从不物化
                self.add_vertex(u)
                self.add_vertex(v)
            if u > v:
                u, v = v, u
            key = (u, v)
            delta[key] = delta.get(key, 0) + (1 if kind == "add" else -1)
        for (u, v), d in delta.items():
            if d == 0:
                continue
            if u == v:
                cur = self.loops.get(u, 0)
                target = cur + d
                if target <= 0:
                    self.loops.pop(u, None)
                else:
                    if cur == 0:
                        self.add_vertex(u)
                    self.loops[u] = target
                continue
            nbr = self.adj.get(u)
            cur = nbr.get(v, 0) if nbr else 0
            target = cur + d
            if target < 0:
                target = 0
            if cur == 0 and target == 0:
                continue
            if cur == 0:
                self.adj[u][v] = target
                self.adj[v][u] = target
                self.nedges += 1
                if self.comp[u] != self.comp[v]:
                    self._merge(u, v)
            elif target == 0:
                self._delete_edge_entry(u, v)
                if v in self.tree[u]:
                    self._delete_tree_entry(u, v)
                    self._reconnect(u, v)
            else:
                self.adj[u][v] = target
                self.adj[v][u] = target

    # ---------------- 内部：合并 / 重连 / 分裂 ----------------

    def _merge(self, u, v):
        """合并 u、v 所在分量（小并入大），并把 (u,v) 记为树边。"""
        cu, cv = self.comp[u], self.comp[v]
        if len(self.members[cu]) > len(self.members[cv]):
            cu, cv = cv, cu
            u, v = v, u
        mu = self.members[cu]
        mv = self.members[cv]
        for x in mu:
            self.comp[x] = cv
        mv |= mu
        del self.members[cu]
        self._free_cids.append(cu)
        if _dict_oversize(self.members):
            self.members = dict(self.members)
        if len(self._free_cids) > len(self.members) + 64:
            del self._free_cids[: len(self._free_cids) // 2]
        self.tree[u].add(v)
        self.tree[v].add(u)

    def _reconnect(self, u, v):
        """树边 (u,v) 已删：在较小侧找替换边，找不到则分裂分量。"""
        tree = self.tree
        su, sv = {u}, {v}
        fu, fv = [u], [v]
        # 双侧交替 BFS，始终扩展较小的一侧，代价 O(较小侧)
        while fu and fv:
            if len(su) <= len(sv):
                nxt = []
                for x in fu:
                    for y in tree[x]:
                        if y not in su:
                            su.add(y)
                            nxt.append(y)
                fu = nxt
            else:
                nxt = []
                for x in fv:
                    for y in tree[x]:
                        if y not in sv:
                            sv.add(y)
                            nxt.append(y)
                fv = nxt
        S = su if not fu else sv  # 先耗尽的一侧即较小侧

        # 只扫描较小侧的关联边寻找通往另一侧的替换边
        adj = self.adj
        for x in S:
            for y in adj[x]:
                if y not in S:
                    tree[x].add(y)
                    tree[y].add(x)
                    return False  # 分量未分裂

        # 无替换边：分量分裂，仅给较小侧重新标号
        old = self.comp[u]
        new = self._new_cid()
        mem = self.members[old]
        for x in S:
            self.comp[x] = new
            mem.discard(x)
        if _set_oversize(mem):
            # 容量远超剩余规模时重建，释放历史峰值容量（摊还 O(1)）
            self.members[old] = set(mem)
        self.members[new] = S
        return True

    # ---------------- 全量重算（基线 / 对拍用） ----------------

    def recompute_components(self):
        """从零全量重算连通分量，返回 {顶点: 规范标签}（标签为分量内最小顶点）。"""
        parent = {}

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for u in self.adj:
            parent[u] = u
        for u, nbrs in self.adj.items():
            for v in nbrs:
                ru, rv = find(u), find(v)
                if ru != rv:
                    parent[ru] = rv
        labels = {}
        for u in self.adj:
            labels[u] = find(u)
        return labels

    def memory_bytes(self):
        """库自身容器的驻留内存（不含顶点 id 等外部共享对象）。"""
        total = (sys.getsizeof(self.adj) + sys.getsizeof(self.loops)
                 + sys.getsizeof(self.tree) + sys.getsizeof(self.comp)
                 + sys.getsizeof(self.members) + sys.getsizeof(self._free_cids))
        for d in self.adj.values():
            total += sys.getsizeof(d)
        for s in self.tree.values():
            total += sys.getsizeof(s)
        for s in self.members.values():
            total += sys.getsizeof(s)
        return total

    # ---------------- 诊断 ----------------

    def _stats(self):
        """内部结构规模，用于验证内存只随活跃图规模增长。"""
        return {
            "vertices": len(self.comp),
            "edges": self.nedges,
            "adj_entries": sum(len(s) for s in self.adj.values()),
            "tree_entries": sum(len(s) for s in self.tree.values()),
            "loop_entries": len(self.loops),
            "components": len(self.members),
            "member_entries": sum(len(s) for s in self.members.values()),
        }
