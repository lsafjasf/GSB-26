"""Aho-Corasick 多模式匹配自动机（纯 Python，仅标准库）。

结构：

* Trie：``next``（dict 转移表）+ ``fail``（失败指针）+ ``out``（本节点
  终止的模式 id，每节点至多一个）+ ``dict_link``（指向最近的、是模式结尾
  的失败链祖先）。``dict_link`` 避免了传统实现里把每个模式输出复制到
  整条失败链上造成的输出重复存储（长前缀词场景下会退化为二次空间）。
* 扫描时沿 fail 回溯转移，输出走 dict_link 链，命中按结束位置有序产生。

非正则、逐词扫描的替代方案：每个文本字符只走一次自动机转移 + 输出链，
时间复杂度 O(n + z)，其中 n 是文本长度，z 是命中个数（含重叠命中）。
"""

from __future__ import annotations

from collections import deque
from typing import Iterator


class AhoCorasick:
    __slots__ = ("next", "fail", "out", "dict_link", "built", "_pattern_count")

    def __init__(self) -> None:
        # 根节点编号为 0
        self.next: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[int | None] = [None]
        self.dict_link: list[int] = [0]
        self.built: bool = False
        self._pattern_count = 0

    @property
    def node_count(self) -> int:
        return len(self.next)

    def add(self, pattern: str, pattern_id: int | None = None) -> int:
        """加入一个模式，返回其终止节点编号。"""
        if self.built:
            raise RuntimeError("automaton already built")
        node = 0
        for ch in pattern:
            nxt = self.next[node]
            child = nxt.get(ch)
            if child is None:
                child = len(self.next)
                nxt[ch] = child
                self.next.append({})
                self.fail.append(0)
                self.out.append(None)
                self.dict_link.append(0)
            node = child
        if self.out[node] is None:
            if pattern_id is None:
                pattern_id = self._pattern_count
            self.out[node] = pattern_id
            self._pattern_count += 1
        return node

    def build(self) -> None:
        """构建失败指针与字典后缀链（BFS）。"""
        if self.built:
            return
        queue: deque[int] = deque()
        for child in self.next[0].values():
            self.fail[child] = 0
            queue.append(child)
        while queue:
            node = queue.popleft()
            f = self.fail[node]
            # dict_link：fail[node] 本身是结尾则指向它，否则继承其 dict_link；
            # 根节点（0）表示“没有输出祖先”。
            self.dict_link[node] = f if self.out[f] is not None else self.dict_link[f]
            for ch, child in self.next[node].items():
                fnode = f
                while fnode != 0 and ch not in self.next[fnode]:
                    fnode = self.fail[fnode]
                self.fail[child] = self.next[fnode].get(ch, 0)
                queue.append(child)
        self.built = True

    def scan(self, text: str) -> Iterator[tuple[int, int]]:
        """扫描文本，产出 ``(end_exclusive, pattern_id)``。

        * ``end_exclusive``：命中在 text 中的结束码点偏移（不含），即
          命中区间为 ``[end_exclusive - len(word), end_exclusive)``；
        * 每个结束位置上，输出链按 *最长模式优先*（dict_link 走到的
          是更短的后缀模式）依次产出；
        * 包含全部重叠：同一起点多词命中、一词是另一词前缀，都会产出。
        """
        if not self.built:
            raise RuntimeError("call build() before scan()")
        transitions = self.next
        fails = self.fail
        outputs = self.out
        dict_links = self.dict_link

        node = 0
        for i, ch in enumerate(text):
            while node != 0 and ch not in transitions[node]:
                node = fails[node]
            node = transitions[node].get(ch, 0)

            out_node = node
            if outputs[out_node] is None:
                out_node = dict_links[out_node]
            while out_node != 0:
                yield i + 1, outputs[out_node]  # type: ignore[misc]
                out_node = dict_links[out_node]
