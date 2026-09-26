"""refcount: 线程安全的引用计数 + 弱引用 + 循环引用检测库（仅标准库）。

核心概念
--------
- ObjectStore: 对象注册表。每个被管理对象对应一个内部 _Handle，
  句柄上维护 strong / external / weak 三个计数与字段边（对象间强引用）。
- Ref:       强引用句柄（外部引用）。clone() 增加计数，release() 减少计数。
- WeakRef:   弱引用。alive() 判断对象是否仍在；get() 原子地升级为强引用，
             对象已销毁时返回 None，绝不返回已销毁对象。
- 环检测:    unreachable() 从所有「外部根」（external > 0 的对象）出发做
             可达性遍历，不可达对象即为可回收垃圾（含循环引用）。
             collect() 强制回收这些对象并返回其 id 列表。

销毁时机与回调顺序
------------------
1. 对象的 strong 计数降到 0 时对象被销毁：先在句柄锁内原子地置 destroyed
   标志（此刻起所有弱引用失效、WeakRef.get() 返回 None），再在锁外按注册
   顺序执行销毁回调，最后释放该对象持有的全部字段边（子对象强计数减一）。
2. 级联销毁按「父先于子」的顺序进行（父的回调先于子的回调）。
3. collect() 回收循环垃圾时：先把环上所有对象标记为 destroyed 并摘除字段
   （保证环内对象互相看到的都是已失效状态），再按对象 id 升序执行回调，
   最后统一释放边。
4. 句柄本身在 strong == 0 且 weak == 0 时从注册表中移除（无泄漏）。

并发模型
--------
- 每个句柄一把锁保护计数与 destroyed 标志；注册表一把锁保护句柄表。
- 锁序：注册表锁 -> 句柄锁，绝不反向嵌套，无死锁。
- 计数增减、弱引用升级均为原子操作；销毁回调在锁外执行，回调中可安全地
  再次操作本库。
"""

import itertools
import threading

__all__ = ["ObjectStore", "Ref", "WeakRef", "DestroyedError"]


class DestroyedError(RuntimeError):
    """对已销毁对象执行强引用操作时抛出。"""


class _Handle:
    __slots__ = (
        "obj_id", "value", "fields", "strong", "external", "weak",
        "destroyed", "callbacks", "lock",
    )

    def __init__(self, obj_id, value):
        self.obj_id = obj_id
        self.value = value
        self.fields = {}          # name -> _Handle（对象持有的内部强引用边）
        self.strong = 0           # 强引用总数（外部 Ref + 内部字段边）
        self.external = 0         # 其中外部 Ref 的数量（可达性根）
        self.weak = 0             # 弱引用数量
        self.destroyed = False
        self.callbacks = []       # 销毁回调，签名 cb(obj_id, value)
        self.lock = threading.Lock()


class Ref:
    """强引用。每个 Ref 必须恰好 release() 一次（或交给 __del__）。"""

    __slots__ = ("_store", "_handle", "_release_lock", "_released")

    def __init__(self, store, handle):
        self._store = store
        self._handle = handle
        self._release_lock = threading.Lock()
        self._released = False
        store._acquire(handle, external=True)

    @classmethod
    def _adopt(cls, store, handle):
        """构造一个不再重复计数的 Ref（计数已由调用方在锁内完成）。"""
        ref = cls.__new__(cls)
        ref._store = store
        ref._handle = handle
        ref._release_lock = threading.Lock()
        ref._released = False
        return ref

    @property
    def id(self):
        return self._handle.obj_id

    def get(self):
        """返回被引用对象的值；对象已销毁则抛 DestroyedError。"""
        handle = self._handle
        with handle.lock:
            if handle.destroyed:
                raise DestroyedError(f"object {handle.obj_id} destroyed")
            return handle.value

    def clone(self):
        """新增一个外部强引用。对象已销毁则抛 DestroyedError。"""
        self._store._acquire(self._handle, external=True)
        return Ref._adopt(self._store, self._handle)

    def set_field(self, name, other):
        """建立 self -> other 的内部强引用边（other 为 Ref）。"""
        child = other._handle
        self._store._acquire(child, external=False)
        old = None
        with self._handle.lock:
            destroyed = self._handle.destroyed
            if not destroyed:
                old = self._handle.fields.get(name)
                self._handle.fields[name] = child
        if destroyed:
            self._store._release(child, external=False)  # 回滚刚加的计数
            raise DestroyedError(f"object {self._handle.obj_id} destroyed")
        if old is not None:
            self._store._release(old, external=False)

    def clear_field(self, name):
        with self._handle.lock:
            old = self._handle.fields.pop(name, None)
        if old is not None:
            self._store._release(old, external=False)

    def on_destroy(self, callback):
        """注册销毁回调；对象已销毁则立即（同步）调用。"""
        call_now = False
        with self._handle.lock:
            if self._handle.destroyed:
                call_now = True
            else:
                self._handle.callbacks.append(callback)
        if call_now:
            callback(self._handle.obj_id, self._handle.value)

    def release(self):
        """释放本引用，幂等。计数减到 0 时触发销毁。"""
        with self._release_lock:
            if self._released:
                return
            self._released = True
        self._store._release(self._handle, external=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


class WeakRef:
    """弱引用。不阻止对象销毁；对象销毁后安全失效。"""

    __slots__ = ("_store", "_handle", "_release_lock", "_released")

    def __init__(self, store, handle):
        self._store = store
        self._handle = handle
        self._release_lock = threading.Lock()
        self._released = False
        with handle.lock:
            handle.weak += 1

    @property
    def id(self):
        return self._handle.obj_id

    def alive(self):
        """对象仍在（未销毁）返回 True。"""
        with self._handle.lock:
            return not self._handle.destroyed

    def get(self):
        """原子升级为强引用 Ref；对象已销毁返回 None（绝不返回死对象）。

        与 collect() 互斥：回收遍历期间升级会被阻塞，保证 collect 的
        可达性快照不会被并发升级推翻（反之升级成功后对象重新成为根）。
        """
        handle = self._handle
        with self._store._collect_lock:
            with handle.lock:
                if handle.destroyed:
                    return None
                handle.strong += 1
                handle.external += 1
        return Ref._adopt(self._store, handle)

    def release(self):
        with self._release_lock:
            if self._released:
                return
            self._released = True
        handle = self._handle
        with handle.lock:
            handle.weak -= 1
            reached_zero = handle.strong == 0 and handle.weak == 0
        if reached_zero:
            self._store._maybe_forget(handle)

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


class ObjectStore:
    """对象注册表与计数/环检测引擎。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._handles = {}
        self._next_id = itertools.count(1)
        # 回收/弱升级互斥锁。锁序：_collect_lock -> _lock -> 句柄锁。
        self._collect_lock = threading.Lock()

    # ---- 创建与查询 -------------------------------------------------

    def create(self, value=None, on_destroy=None):
        """创建对象，返回持有它的外部强引用 Ref。"""
        with self._lock:
            obj_id = next(self._next_id)
            handle = _Handle(obj_id, value)
            self._handles[obj_id] = handle
        if on_destroy is not None:
            handle.callbacks.append(on_destroy)
        return Ref(self, handle)

    def _find(self, ref_or_id):
        obj_id = ref_or_id.id if isinstance(ref_or_id, (Ref, WeakRef)) else ref_or_id
        with self._lock:
            return self._handles.get(obj_id)

    def strong_count(self, ref_or_id):
        handle = self._find(ref_or_id)
        if handle is None:
            return 0
        with handle.lock:
            return handle.strong

    def weak_count(self, ref_or_id):
        handle = self._find(ref_or_id)
        if handle is None:
            return 0
        with handle.lock:
            return handle.weak

    def is_destroyed(self, ref_or_id):
        handle = self._find(ref_or_id)
        if handle is None:
            return True
        with handle.lock:
            return handle.destroyed

    def live_count(self):
        """注册表中仍存活的句柄数（泄漏检查用）。"""
        with self._lock:
            return len(self._handles)

    # ---- 计数原语 ---------------------------------------------------

    def _acquire(self, handle, external):
        with handle.lock:
            if handle.destroyed:
                raise DestroyedError(f"object {handle.obj_id} destroyed")
            handle.strong += 1
            if external:
                handle.external += 1

    def _release(self, handle, external):
        # 迭代式级联释放，避免长链递归爆栈。
        stack = [(handle, external)]
        while stack:
            current, is_external = stack.pop()
            callbacks = None
            edges = None
            reached_zero = False
            with current.lock:
                current.strong -= 1
                if is_external:
                    current.external -= 1
                if current.strong < 0 or current.external < 0:
                    raise RuntimeError(
                        f"refcount underflow on object {current.obj_id}")
                if current.strong == 0:
                    reached_zero = True
                    if not current.destroyed:
                        # 先在锁内原子失效（弱引用立即不可升级），
                        # 再在锁外执行回调、释放子边。
                        current.destroyed = True
                        callbacks = list(current.callbacks)
                        edges = list(current.fields.values())
                        current.fields.clear()
            if callbacks is not None:
                for cb in callbacks:
                    cb(current.obj_id, current.value)
                for child in edges:
                    stack.append((child, False))
            if reached_zero:
                self._maybe_forget(current)

    def _maybe_forget(self, handle):
        # 锁序：注册表锁 -> 句柄锁（全库唯一嵌套点，方向固定）。
        with self._lock:
            with handle.lock:
                if handle.strong == 0 and handle.weak == 0:
                    self._handles.pop(handle.obj_id, None)

    # ---- 弱引用 -----------------------------------------------------

    def weak(self, ref):
        """为 ref 指向的对象创建弱引用。"""
        return WeakRef(self, ref._handle)

    # ---- 环检测与回收 -------------------------------------------------

    def unreachable(self):
        """可达性遍历：从所有外部根出发，返回不可达（可回收）对象 id 列表。

        已销毁（等待弱引用清零）的句柄不算可回收垃圾，会被跳过。
        """
        with self._collect_lock:
            return self._unreachable_locked()

    def _unreachable_locked(self):
        with self._lock:
            handles = list(self._handles.values())
        snapshot = {}
        for handle in handles:
            with handle.lock:
                if handle.destroyed:
                    continue
                snapshot[handle.obj_id] = (
                    handle.external,
                    [child.obj_id for child in handle.fields.values()],
                )
        seen = set()
        stack = [hid for hid, (ext, _) in snapshot.items() if ext > 0]
        while stack:
            hid = stack.pop()
            if hid in seen:
                continue
            seen.add(hid)
            stack.extend(snapshot.get(hid, (0, []))[1])
        return sorted(hid for hid in snapshot if hid not in seen)

    def collect(self):
        """回收所有不可达对象（含循环引用），返回被回收对象 id 列表。

        顺序：先整体标记 destroyed 并摘除字段边（环内对象同步失效），
        再按 id 升序执行销毁回调，最后统一释放边、清理注册表。
        """
        with self._collect_lock:
            garbage_ids = self._unreachable_locked()
            if not garbage_ids:
                return []
            with self._lock:
                garbage = [self._handles[gid] for gid in garbage_ids
                           if gid in self._handles]
            marked = []
            pending_edges = []
            for handle in garbage:
                with handle.lock:
                    if handle.destroyed:
                        continue
                    handle.destroyed = True
                    pending_edges.extend(handle.fields.values())
                    handle.fields.clear()
                    marked.append(handle)
            for handle in marked:  # 回调按 id 升序，确定性好
                for cb in list(handle.callbacks):
                    cb(handle.obj_id, handle.value)
            for child in pending_edges:
                self._release(child, external=False)
            for handle in marked:
                self._maybe_forget(handle)
            return [handle.obj_id for handle in marked]
