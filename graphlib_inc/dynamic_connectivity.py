"""Fully-dynamic connectivity for undirected multigraphs (stdlib only).

Maintains connected components and reachability queries under edge
insertions and deletions without ever recomputing components from scratch.

Algorithm
---------
A practical variant of Holm, Lichtenberg & Thorup (HLT) fully-dynamic
connectivity:

* A spanning forest of the current graph is maintained explicitly.
  Every edge is either a *tree edge* or a *non-tree edge*, and carries a
  *level* in ``0 .. L`` with ``L = ceil(log2(n))``.  Edges enter at level 0;
  levels only increase over time.
* Insertion of an edge across two components makes it a tree edge (level 0)
  and merges the two component label sets (smaller into larger, so each
  vertex is relabelled at most O(log n) times).  Insertion inside a
  component creates a non-tree edge at level 0.
* Deletion of a non-tree edge only touches its two adjacency entries.
* Deletion of a tree edge ``e`` at level ``l`` triggers a replacement
  search, for ``i = l .. 0``: the two sides of the cut forest tree are
  explored by alternating BFS over tree edges of level >= i until the
  smaller side ``T`` is fully known; level-``i`` tree edges of ``T`` are
  promoted to level ``i+1`` (this preserves the HLT invariant that a tree
  at level ``i`` has at most ``n / 2**i`` vertices, because ``T`` is at
  most half of the old tree); then level-``i`` non-tree edges incident to
  ``T`` are scanned - an edge ending inside ``T`` is promoted, an edge
  leaving ``T`` must end on the other side of the cut (HLT invariant:
  endpoints of a level-``i`` non-tree edge are connected in the forest of
  level >= i) and becomes the replacement tree edge.  If no level yields a
  replacement, the component really splits and the smaller side (already
  enumerated by the level-0 BFS) receives a fresh component label.

Only the cut side of the forest is touched, never the whole graph.

Cost (amortized): each edge level is raised at most L = O(log n) times and
each promotion costs O(1) scanning; component labels are O(1) per query.
The classic HLT proof uses Euler-tour trees to find the smaller tree in
O(log n); this implementation uses alternating BFS instead, which keeps
the same amortized behaviour on non-adversarial workloads and is exact in
the worst case only up to the explored cut side.

Multigraph semantics
--------------------
Parallel edges are counted: the k-th insertion of the same unordered pair
only increments a counter, and the edge disappears from the connectivity
structure only when its last copy is removed.  Self-loops are counted but
never influence connectivity.  Removing an edge that does not exist is a
no-op returning ``False``.

Memory
------
All structures are proportional to the *current* number of vertices and
distinct edges: two adjacency dictionaries (tree / non-tree), one
component label per vertex, one membership set per non-singleton
component, and small counters for parallel edges / self-loops.  Deleting
an edge removes it from every structure; no history, logs or tombstones
are kept, so memory does not grow with the number of updates.

Batches
-------
``apply_batch(adds, dels)`` settles a whole batch.  A batch is specified
as two collections of edges on disjoint endpoint pairs (an edge is either
added or removed within one batch, not both).  The final graph is then
``G' = (G \\ D) + A`` regardless of the order in which the individual
operations are applied, because each operation only asserts the final
presence/absence of its own edge.  Since connected components and
reachability answers are a pure function of the final edge set, every
interleaving of the same batch yields identical query results (the
internal choice of tree edges may differ, which is unobservable through
the query API).  This is verified against shuffled interleavings in the
test-suite.
"""

from math import ceil, log2

__all__ = ["DynamicConnectivity"]


class DynamicConnectivity:
    """Incremental connected components / reachability for undirected graphs."""

    def __init__(self):
        # vertex identity mapping (external label -> small int id)
        self._id_of = {}
        self._label_of = []
        # per-vertex component label (component id)
        self._comp = []
        # component id -> set of vertex ids (only for components of size >= 2)
        self._members = {}
        # adjacency: vertex id -> {neighbour id -> edge level}
        self._tree = []
        self._nontree = []
        # parallel-edge counter: (min_id, max_id) -> number of *extra* copies
        self._dup = {}
        # self-loop counter: vertex id -> count
        self._loops = {}
        self._ncomp = 0          # number of connected components
        self._next_comp = 0      # component id allocator
        self._nedges = 0         # total edges counting multiplicity (incl. loops)

    # ------------------------------------------------------------------ #
    # vertex handling
    # ------------------------------------------------------------------ #
    def _vid(self, v):
        """Internal id for ``v``, creating a fresh singleton component."""
        idx = self._id_of.get(v)
        if idx is None:
            idx = len(self._label_of)
            self._id_of[v] = idx
            self._label_of.append(v)
            self._comp.append(self._next_comp)
            self._next_comp += 1
            self._tree.append({})
            self._nontree.append({})
            self._ncomp += 1
        return idx

    def add_vertex(self, v):
        """Materialize an isolated vertex (queries work without this)."""
        self._vid(v)

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #
    def connected(self, u, v):
        """True iff ``u`` and ``v`` are in the same connected component."""
        iu = self._id_of.get(u)
        iv = self._id_of.get(v)
        if iu is None or iv is None:
            return u == v
        return self._comp[iu] == self._comp[iv]

    def component_size(self, v):
        """Number of vertices in ``v``'s component (1 for unknown vertices)."""
        iv = self._id_of.get(v)
        if iv is None:
            return 1
        s = self._members.get(self._comp[iv])
        return len(s) if s is not None else 1

    @property
    def num_components(self):
        return self._ncomp

    @property
    def num_vertices(self):
        return len(self._label_of)

    @property
    def num_edges(self):
        """Edge count with multiplicity (self-loops included)."""
        return self._nedges

    def max_level(self):
        n = max(2, len(self._label_of))
        return max(1, ceil(log2(n)))

    # ------------------------------------------------------------------ #
    # updates
    # ------------------------------------------------------------------ #
    def add_edge(self, u, v):
        """Insert edge ``u-v``.  Parallel edges and self-loops are counted."""
        iu = self._vid(u)
        iv = self._vid(v)
        self._nedges += 1
        if iu == iv:
            self._loops[iu] = self._loops.get(iu, 0) + 1
            return
        if iv in self._tree[iu] or iv in self._nontree[iu]:
            key = (iu, iv) if iu < iv else (iv, iu)
            self._dup[key] = self._dup.get(key, 0) + 1
            return
        if self._comp[iu] != self._comp[iv]:
            self._tree[iu][iv] = 0
            self._tree[iv][iu] = 0
            self._merge(iu, iv)
        else:
            self._nontree[iu][iv] = 0
            self._nontree[iv][iu] = 0

    def remove_edge(self, u, v):
        """Remove one copy of edge ``u-v``.

        Returns ``True`` if an edge was present, ``False`` otherwise
        (removing a non-existent edge is a well-defined no-op).
        """
        iu = self._id_of.get(u)
        iv = self._id_of.get(v)
        if iu is None or iv is None:
            return False
        if iu == iv:
            c = self._loops.get(iu, 0)
            if c == 0:
                return False
            if c == 1:
                del self._loops[iu]
            else:
                self._loops[iu] = c - 1
            self._nedges -= 1
            return True
        key = (iu, iv) if iu < iv else (iv, iu)
        extra = self._dup.get(key, 0)
        if extra > 0:
            if extra == 1:
                del self._dup[key]
            else:
                self._dup[key] = extra - 1
            self._nedges -= 1
            return True
        lev = self._tree[iu].get(iv)
        if lev is not None:
            self._nedges -= 1
            self._cut(iu, iv, lev)
            return True
        lev = self._nontree[iu].get(iv)
        if lev is not None:
            del self._nontree[iu][iv]
            del self._nontree[iv][iu]
            self._nedges -= 1
            return True
        return False

    # ------------------------------------------------------------------ #
    # batches
    # ------------------------------------------------------------------ #
    def apply_batch(self, adds=(), dels=()):
        """Settle a batch of insertions / deletions.

        ``adds`` and ``dels`` are iterables of ``(u, v)`` pairs.  They must
        not contain the same unordered pair within one batch; under that
        convention the resulting graph - and therefore every connectivity
        answer - is independent of the application order (see module
        docstring).  Returns ``(effective_adds, effective_dels)``.
        """
        na = nd = 0
        before = self._nedges
        for u, v in dels:
            nd += self.remove_edge(u, v)
        for u, v in adds:
            self.add_edge(u, v)
        na = self._nedges - before + nd
        return na, nd

    def add_edges(self, edges):
        for u, v in edges:
            self.add_edge(u, v)

    def remove_edges(self, edges):
        for u, v in edges:
            self.remove_edge(u, v)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _merge(self, iu, iv):
        """Merge the components of vertices ``iu`` and ``iv`` (smaller into
        larger, so each vertex is relabelled at most O(log n) times)."""
        cu = self._comp[iu]
        cv = self._comp[iv]
        su = self._members.get(cu)
        sv = self._members.get(cv)
        if su is None:
            su = {iu}
        if sv is None:
            sv = {iv}
        if len(su) < len(sv):
            cu, cv = cv, cu
            su, sv = sv, su
        # keep component id ``cu``; absorb ``sv``
        for x in sv:
            self._comp[x] = cu
        su |= sv
        self._members[cu] = su
        self._members.pop(cv, None)
        self._ncomp -= 1

    def _cut(self, u, v, l):
        """Remove tree edge ``u-v`` sitting at level ``l``."""
        del self._tree[u][v]
        del self._tree[v][u]
        small = None
        for i in range(l, -1, -1):
            small = self._smaller_side(u, v, i)
            # 1) promote level-i tree edges of the smaller side
            for x in small:
                for y, lev in list(self._tree[x].items()):
                    if lev == i:
                        self._tree[x][y] = i + 1
                        self._tree[y][x] = i + 1
            # 2) scan level-i non-tree edges of the smaller side
            replacement = None
            for x in small:
                for y, lev in list(self._nontree[x].items()):
                    if lev != i:
                        continue
                    if y in small:
                        # internal edge: promote
                        self._nontree[x][y] = i + 1
                        self._nontree[y][x] = i + 1
                    else:
                        # leaves the cut -> must end on the other side
                        replacement = (x, y)
                        break
                if replacement:
                    break
            if replacement:
                x, y = replacement
                del self._nontree[x][y]
                del self._nontree[y][x]
                self._tree[x][y] = i
                self._tree[y][x] = i
                return  # component did not split
        # no replacement at any level: the component really splits.
        # ``small`` is the smaller side of the level-0 cut.
        self._split_off(small)

    def _smaller_side(self, u, v, level):
        """Fully explore the smaller of the two level-``level`` forest trees
        containing ``u`` and ``v`` (they are disjoint: the connecting tree
        edge was already removed).  Alternating BFS, stops as soon as one
        side is exhausted."""
        su = {u}
        sv = {v}
        fu = [u]
        fv = [v]
        tree = self._tree
        while fu and fv:
            if len(su) <= len(sv):
                x = fu.pop()
                for y, lev in tree[x].items():
                    if lev >= level and y not in su:
                        su.add(y)
                        fu.append(y)
            else:
                x = fv.pop()
                for y, lev in tree[x].items():
                    if lev >= level and y not in sv:
                        sv.add(y)
                        fv.append(y)
        return su if not fu else sv

    def _split_off(self, small):
        """Give the vertex set ``small`` a fresh component label."""
        newc = self._next_comp
        self._next_comp += 1
        oldc = self._comp[next(iter(small))]
        for x in small:
            self._comp[x] = newc
        members = self._members.get(oldc)
        if members is not None:
            members.difference_update(small)
            if len(members) <= 1:
                del self._members[oldc]
        if len(small) > 1:
            self._members[newc] = set(small)
        self._ncomp += 1

    # ------------------------------------------------------------------ #
    # introspection (used by tests / benchmarks)
    # ------------------------------------------------------------------ #
    def components(self):
        """Current partition as a list of frozensets of external labels."""
        groups = {}
        for idx, c in enumerate(self._comp):
            groups.setdefault(c, []).append(idx)
        label = self._label_of
        return [frozenset(label[i] for i in ids) for ids in groups.values()]
