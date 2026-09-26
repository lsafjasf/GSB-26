"""Aho-Corasick 多模式匹配自动机（标准库实现）。

命中规则（重要）：
  - 返回全部命中，包括重叠与同一起点的多个命中。
    例如词表 {"he", "she", "her"}，文本 "she" 会返回
    she@[0,3) 和 he@[1,3) 两条命中。
  - finditer 按 (end, start) 顺序产出（即扫描顺序：先按终点，同终点按
    字典构建时输出链顺序——短词/后缀先出）。find_all 返回按
    (start, end, word) 排序后的稳定列表，便于对拍与断言。
"""

from collections import deque


class AhoCorasick:
    def __init__(self):
        # 每个节点: (transitions: dict[str, int], fail: int, outputs: list[str])
        self._trans = [{}]
        self._fail = [0]
        self._out = [[]]
        self._built = False

    def add(self, word):
        if not word:
            raise ValueError("empty pattern is not allowed")
        node = 0
        for ch in word:
            nxt = self._trans[node].get(ch)
            if nxt is None:
                nxt = len(self._trans)
                self._trans.append({})
                self._fail.append(0)
                self._out.append([])
                self._trans[node][ch] = nxt
            node = nxt
        if word not in self._out[node]:
            self._out[node].append(word)
        self._built = False

    def add_all(self, words):
        for w in words:
            self.add(w)

    def build(self):
        """BFS 构建失败指针，并把 fail 节点的输出合并到当前节点。"""
        queue = deque()
        for ch, nxt in self._trans[0].items():
            self._fail[nxt] = 0
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            fail = self._fail[node]
            # 合并失败节点的输出（保证一个词是另一个词后缀时也能命中）
            if self._out[fail]:
                merged = list(self._out[node])
                for w in self._out[fail]:
                    if w not in merged:
                        merged.append(w)
                self._out[node] = merged
            for ch, nxt in self._trans[node].items():
                # 沿 fail 链找到能接收 ch 的节点
                f = fail
                while f and ch not in self._trans[f]:
                    f = self._fail[f]
                self._fail[nxt] = self._trans[f].get(ch, 0)
                queue.append(nxt)
        self._built = True

    def finditer(self, text):
        """扫描文本，产出 (start, end, word)，end 为开区间。O(n + 命中数)。"""
        if not self._built:
            self.build()
        node = 0
        trans, fail, out = self._trans, self._fail, self._out
        for i, ch in enumerate(text):
            while node and ch not in trans[node]:
                node = fail[node]
            node = trans[node].get(ch, 0)
            for word in out[node]:
                yield i - len(word) + 1, i + 1, word

    def find_all(self, text):
        """返回按 (start, end, word) 排序的命中列表。"""
        return sorted(self.finditer(text), key=lambda m: (m[0], m[1], m[2]))

    @property
    def num_nodes(self):
        return len(self._trans)
