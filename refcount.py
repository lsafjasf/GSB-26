"""
refcount.py — 引用计数 + 弱引用 + 循环引用检测管理库（仅标准库）。

核心概念
--------
- Managed        : 被管理对象。持有强引用计数、子对象强引用边、销毁回调。
- WeakRef        : 弱引用。对象销毁后安全失效，get() 返回 None，绝不返回已销毁对象。
- find_cycles()  : 基于 Tarjan SCC 的环检测，报告每个环涉及的对象及是否可回收。
- collect()      : 从根集合做可达性遍历，回收不可达对象（含环），返回被回收对象。

销毁时机与回调顺序
------------------
- 对象在强引用计数降为 0 的「那一次 release()」内同步销毁（确定性析构）。
- 销毁是级联且迭代的（显式栈，不耗递归栈）：先标记对象已销毁、使弱引用失效，
  再执行该对象自身的回调（按注册顺序），最后释放它对子对象的强引用。
- 因此回调顺序保证：父对象回调先于其子对象回调；同一对象内按注册顺序。
- collect() 回收不可达环时，先断开环内所有内部边，再逐个销毁，回调顺序按
  发现顺序，同样保证「回调运行时该对象的弱引用已失效」。

线程安全
--------
- 所有计数增减、边修改、销毁级联都在同一把可重入全局锁 _LOCK 内完成，
  计数不会漂移，对象不会在仍有强引用时被销毁。
- 不变量（测试断言）：
    I1: 任意时刻 strong_count >= 0
    I2: strong_count > 0  =>  alive == True（不会提前销毁）
    I3: alive == False    =>  strong_count == 0 且 WeakRef.get() is None
"""

from __future__ import annotations

import threading
from collections import deque

_LOCK = threading.RLock()

# 所有存活对象的注册表：id -> Managed（用于环检测 / 泄漏检查）
_LIVE: dict[int, "Managed"] = {}


def live_count() -> int:
    """当前存活（未销毁）的被管理对象数量。用于泄漏检查。"""
    with _LOCK:
        return len(_LIVE)


def live_objects() -> list["Managed"]:
    with _LOCK:
        return list(_LIVE.values())


class ObjectDestroyedError(RuntimeError):
    """访问已销毁对象的值时抛出。"""


class Managed:
    """被引用计数管理的对象。"""

    __slots__ = ("_value", "_strong", "_weak", "_alive", "_children", "_callbacks", "name")

    def __init__(self, value=None, name: str | None = None):
        with _LOCK:
            self._value = value
            self._strong = 1          # 创建者持有第一个强引用
            self._weak = 0            # 指向本对象的弱引用数量
            self._alive = True
            self._children: list[Managed] = []   # 本对象持有的强引用边
            self._callbacks: list = []
            self.name = name
            _LIVE[id(self)] = self

    # ---------------- 计数查询 ----------------
    @property
    def strong_count(self) -> int:
        with _LOCK:
            return self._strong

    @property
    def weak_count(self) -> int:
        with _LOCK:
            return self._weak

    @property
    def alive(self) -> bool:
        with _LOCK:
            return self._alive

    @property
    def value(self):
        with _LOCK:
            if not self._alive:
                raise ObjectDestroyedError(f"object {self!r} has been destroyed")
            return self._value

    @property
    def children(self) -> tuple["Managed", ...]:
        with _LOCK:
            return tuple(self._children)

    # ---------------- 强引用 ----------------
    def retain(self) -> "Managed":
        """增加一个强引用。返回 self 便于链式调用。"""
        with _LOCK:
            if not self._alive:
                raise ObjectDestroyedError(f"cannot retain destroyed object {self!r}")
            self._strong += 1
            return self

    def release(self) -> None:
        """释放一个强引用；计数归零时同步销毁并级联释放子对象。"""
        with _LOCK:
            if self._strong <= 0:
                raise RuntimeError(f"release() on {self!r} with strong_count == 0")
            self._strong -= 1
            if self._strong == 0:
                _destroy_cascade(self)

    # ---------------- 对象图 ----------------
    def add_child(self, other: "Managed") -> "Managed":
        """建立一条本对象 -> other 的强引用边（other 计数 +1）。"""
        with _LOCK:
            if not self._alive:
                raise ObjectDestroyedError(f"cannot add child on destroyed object {self!r}")
            other.retain()
            self._children.append(other)
            return other

    def remove_child(self, other: "Managed") -> None:
        with _LOCK:
            self._children.remove(other)
            other.release()

    def on_destroy(self, callback) -> None:
        """注册销毁回调，回调签名 callback(obj)，按注册顺序执行。"""
        with _LOCK:
            if not self._alive:
                raise ObjectDestroyedError(f"cannot add callback on destroyed object {self!r}")
            self._callbacks.append(callback)

    def __repr__(self):
        label = self.name if self.name is not None else hex(id(self))
        state = "alive" if self._alive else "dead"
        return f"<Managed {label} strong={self._strong} {state}>"


def _destroy_cascade(obj: Managed) -> None:
    """迭代式销毁级联（调用时必须持有 _LOCK，且 obj._strong 已为 0）。

    顺序：标记死亡 -> 执行自身回调 -> 递减子对象计数（归零则入栈）。
    用显式栈避免长链递归爆栈。
    """
    stack = [obj]
    while stack:
        current = stack.pop()
        if not current._alive:
            continue
        current._alive = False
        _LIVE.pop(id(current), None)
        callbacks, children = current._callbacks, current._children
        current._callbacks, current._children = [], []
        for cb in callbacks:
            cb(current)
        for child in children:
            child._strong -= 1
            if child._strong == 0:
                stack.append(child)


class WeakRef:
    """弱引用：不增加强计数；对象销毁后 get() 返回 None。"""

    __slots__ = ("_target",)

    def __init__(self, target: Managed):
        with _LOCK:
            if not target._alive:
                raise ObjectDestroyedError(f"cannot create WeakRef to destroyed {target!r}")
            target._weak += 1
            self._target = target

    def get(self) -> Managed | None:
        """对象仍在则返回对象，已释放则返回 None。绝不返回已销毁对象。"""
        with _LOCK:
            return self._target if self._target._alive else None

    @property
    def alive(self) -> bool:
        with _LOCK:
            return self._target._alive

    def close(self) -> None:
        """主动放弃弱引用（递减目标的弱计数）。"""
        with _LOCK:
            if self._target is not None:
                self._target._weak -= 1
                self._target = None

    def __repr__(self):
        state = "alive" if self.alive else "dead"
        return f"<WeakRef {state}>"


# ---------------- 环检测与回收 ----------------

def _in_degree() -> dict[int, int]:
    """每个存活对象被其它存活对象强引用的次数（图内入度）。"""
    deg: dict[int, int] = {oid: 0 for oid in _LIVE}
    for obj in _LIVE.values():
        for child in obj._children:
            if id(child) in deg:
                deg[id(child)] += 1
    return deg


def auto_roots() -> list[Managed]:
    """自动推断根：强计数大于图内入度的对象，说明有外部（非 Managed）强引用。"""
    with _LOCK:
        deg = _in_degree()
        return [o for oid, o in _LIVE.items() if o._strong > deg[oid]]


def reachable_from(roots) -> set[int]:
    """从根集合出发沿强引用边可达的对象 id 集合。"""
    with _LOCK:
        seen: set[int] = set()
        queue = deque(r for r in roots if r._alive)
        while queue:
            obj = queue.popleft()
            if id(obj) in seen or not obj._alive:
                continue
            seen.add(id(obj))
            queue.extend(obj._children)
        return seen


def unreachable(roots=None) -> list[Managed]:
    """不可达（可回收）对象列表。roots 为 None 时自动推断根。"""
    with _LOCK:
        if roots is None:
            roots = auto_roots()
        seen = reachable_from(roots)
        return [o for oid, o in _LIVE.items() if oid not in seen]


def find_cycles() -> list[dict]:
    """Tarjan SCC 环检测。

    返回列表，每项：
        {"objects": [Managed, ...], "reclaimable": bool}
    reclaimable=True 表示该环不可达（可被 collect 回收）；
    False 表示环仍被外部根引用，确实不可回收，调用方可据此报告涉及对象。
    """
    with _LOCK:
        index_of: dict[int, int] = {}
        lowlink: dict[int, int] = {}
        on_stack: set[int] = set()
        stack: list[Managed] = []
        sccs: list[list[Managed]] = []
        counter = [0]

        # 迭代版 Tarjan（避免长链递归）
        for start in list(_LIVE.values()):
            if id(start) in index_of:
                continue
            work = [(start, 0)]
            while work:
                node, ci = work[-1]
                if ci == 0:
                    index_of[id(node)] = lowlink[id(node)] = counter[0]
                    counter[0] += 1
                    stack.append(node)
                    on_stack.add(id(node))
                recursed = False
                children = node._children
                while ci < len(children):
                    child = children[ci]
                    ci += 1
                    if not child._alive:
                        continue
                    if id(child) not in index_of:
                        work[-1] = (node, ci)
                        work.append((child, 0))
                        recursed = True
                        break
                    elif id(child) in on_stack:
                        lowlink[id(node)] = min(lowlink[id(node)], index_of[id(child)])
                if recursed:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[id(parent)] = min(lowlink[id(parent)], lowlink[id(node)])
                if lowlink[id(node)] == index_of[id(node)]:
                    scc = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(id(w))
                        scc.append(w)
                        if w is node:
                            break
                    if len(scc) > 1 or any(c is scc[0] for c in scc[0]._children):
                        sccs.append(scc)

        roots = auto_roots()
        reach = reachable_from(roots)
        return [
            {"objects": scc, "reclaimable": all(id(o) not in reach for o in scc)}
            for scc in sccs
        ]


def collect(roots=None) -> list[Managed]:
    """回收所有不可达对象（含不可达环）。返回被回收对象列表。"""
    with _LOCK:
        dead = unreachable(roots)
        if not dead:
            return []
        dead_ids = {id(o) for o in dead}
        # 第一步：断开不可达集合内部的边（只减计数，不触发销毁）
        for obj in dead:
            internal = [c for c in obj._children if id(c) in dead_ids]
            for c in internal:
                c._strong -= 1
            obj._children = [c for c in obj._children if id(c) not in dead_ids]
        # 不可达 => 无外部强引用 => 此时集合内所有对象计数均为 0
        for obj in dead:
            assert obj._strong == 0, f"{obj!r} still has strong refs"
        # 第二步：逐个销毁（回调照常触发，弱引用照常失效）
        for obj in dead:
            _destroy_cascade(obj)
        return dead
