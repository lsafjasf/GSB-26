"""修复后的任务执行器（仅依赖 Python 标准库）。

修复要点：
- ResourcePool.release 幂等：同一批资源释放任意次数实际只生效一次，
  计数永不失真（修复“超时 + 取消”竞争导致的二次释放 / EBADF）。
- 资源释放、等待队列出队、并发额度归还集中在唯一的同步清理路径
  _finalize 中，在 finally 中调用。同步清理不可被取消打断，因此任务
  正常完成、异常退出、超时、被取消四种结束方式都会完整清理（修复
  取消后缓冲泄漏与等待队列残留）。
- 外层取消路径在清理前尽力等待任务体响应取消（shield 循环，避免
  外层处于“正在取消”状态时 await 被立即重放而跳过等待）。
- 资源计数不变量（可在任意时刻断言，见 test_resource_invariants）：
      pool.allocated - pool.released == len(pool 持有的未释放资源)
      且该差值 == executor.in_flight_count（在途任务数）
  即：申请数与释放数在任意时刻的差值，恒等于在途任务数。
"""

import asyncio
import contextlib
import os


class ResourcePool:
    """按任务申请缓冲（bytearray）与句柄（os.pipe 的 fd 对）。"""

    def __init__(self, check_invariant=True):
        self._live = set()
        self.allocated = 0
        self.released = 0
        self.check_invariant = check_invariant

    @property
    def in_flight(self):
        return len(self._live)

    def acquire(self):
        read_fd, write_fd = os.pipe()
        os.set_inheritable(read_fd, False)
        os.set_inheritable(write_fd, False)
        resource = {"buffer": bytearray(4096), "fds": [read_fd, write_fd],
                    "released": False}
        self._live.add(id(resource))
        self.allocated += 1
        self._assert_invariant()
        return resource

    def release(self, resource):
        """幂等释放：返回 True 表示本次实际释放，False 表示此前已释放。"""
        if resource.get("released"):
            return False
        resource["released"] = True
        for fd in resource["fds"]:
            os.close(fd)
        self._live.discard(id(resource))
        self.released += 1
        self._assert_invariant()
        return True

    def _assert_invariant(self):
        if self.check_invariant:
            assert self.allocated - self.released == self.in_flight


class TaskExecutor:
    def __init__(self, max_concurrent=8, timeout=0.05, check_invariant=True):
        self.pool = ResourcePool(check_invariant=check_invariant)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._waiting = set()   # 已开始执行、尚未完成清理的任务
        self._inflight = set()  # 已申请资源、尚未释放资源的任务
        self.timeout = timeout

    @property
    def waiting_count(self):
        return len(self._waiting)

    @property
    def in_flight_count(self):
        return len(self._inflight)

    async def submit(self, coro):
        # 排队阶段被取消：尚未申请任何资源，关闭协程后直接传播
        try:
            await self._semaphore.acquire()
        except asyncio.CancelledError:
            coro.close()
            raise

        task = asyncio.ensure_future(coro)
        self._waiting.add(task)
        resource = self.pool.acquire()
        self._inflight.add(task)
        try:
            async with asyncio.timeout(self.timeout):
                return await task
        except TimeoutError:
            # asyncio.timeout 已取消 task，等待取消在任务体内落地
            await self._drain(task)
            raise
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await self._drain(task)
            raise
        except BaseException:
            # 任务自身抛出异常：确保任务落地（正常情况下已 done）
            if not task.done():
                task.cancel()
            await self._drain(task)
            raise
        finally:
            # 唯一的、同步且不可打断的清理路径
            self._finalize(task, resource)

    @staticmethod
    async def _drain(task):
        """等待 task 结束并回收其结果/异常。

        调用方自身可能处于“正在取消”状态，直接 await 会立即重放
        CancelledError，因此用 shield 循环，直到 task 真正 done。"""
        while not task.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        if not task.cancelled():
            with contextlib.suppress(Exception):
                task.exception()  # 标记异常已回收，避免事件循环告警

    def _finalize(self, task, resource):
        self.pool.release(resource)  # 幂等，重复调用安全
        self._inflight.discard(task)
        self._waiting.discard(task)
        self._semaphore.release()
