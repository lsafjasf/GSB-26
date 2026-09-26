"""修复后执行器的回归测试（Python 标准库 unittest）。

覆盖四类现网场景及其组合，并断言资源计数不变量：
    pool.allocated - pool.released == pool.in_flight == executor.in_flight_count
"""

import asyncio
import contextlib
import gc
import os
import unittest

from task_executor.executor import ResourcePool, TaskExecutor


def open_fd_count():
    """当前进程持有的句柄（fd）数。"""
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


def _fd_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


async def sleep_task(delay):
    await asyncio.sleep(delay)
    return "ok"


async def error_task(delay=0):
    await asyncio.sleep(delay)
    raise RuntimeError("boom")


class SwallowCancellation:
    """吞掉 CancelledError 的任务体（模拟不规范用户任务）。"""

    def __init__(self, delay, swallow=100):
        self.delay = delay
        self.swallow = swallow

    async def __call__(self):
        for _ in range(self.swallow + 1):
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                if self.swallow > 0:
                    self.swallow -= 1
                    continue
                raise
        return "survived"


async def consume(coro, cancel=False, cancel_after=0.01):
    """运行一个 submit 调用；cancel=True 时模拟外层取消。"""
    task = asyncio.ensure_future(coro)
    if cancel:
        await asyncio.sleep(cancel_after)
        task.cancel()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await task


class ExecutorTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        gc.collect()
        await asyncio.sleep(0.02)
        self._baseline_fds = open_fd_count()

    async def test_cancel_releases_buffers_and_handles(self):
        executor = TaskExecutor(max_concurrent=4, timeout=10)
        handles = [asyncio.ensure_future(
            executor.submit(sleep_task(10))) for _ in range(4)]
        await asyncio.sleep(0.02)
        self.assertEqual(executor.in_flight_count, 4)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)

        self.assertEqual(executor.pool.in_flight, 0)
        self.assertEqual(executor.pool.allocated, executor.pool.released)
        self.assertEqual(executor.waiting_count, 0)
        self.assertEqual(executor.in_flight_count, 0)

    async def test_timeout_does_not_double_release(self):
        executor = TaskExecutor(max_concurrent=4, timeout=0.02)
        results = await asyncio.gather(
            *[executor.submit(sleep_task(1)) for _ in range(8)],
            return_exceptions=True)
        self.assertTrue(all(isinstance(r, TimeoutError) for r in results))

        self.assertEqual(executor.pool.allocated, executor.pool.released)
        self.assertEqual(executor.pool.in_flight, 0)
        self.assertEqual(executor.waiting_count, 0)
        gc.collect()
        self.assertEqual(open_fd_count(), self._baseline_fds)

    async def test_timeout_and_cancel_together_single_release(self):
        """超时看门狗与外层取消在同一时刻触发：只能释放一次。"""
        for _ in range(100):
            executor = TaskExecutor(max_concurrent=2, timeout=0.02)
            handles = [asyncio.ensure_future(
                executor.submit(sleep_task(10))) for _ in range(2)]
            await asyncio.sleep(0.02)  # 与超时同时
            for handle in handles:
                handle.cancel()
            await asyncio.gather(*handles, return_exceptions=True)

            self.assertEqual(executor.pool.allocated, executor.pool.released)
            self.assertEqual(executor.pool.in_flight, 0)
            self.assertEqual(executor.waiting_count, 0)
            self.assertEqual(executor.in_flight_count, 0)

    async def test_exception_clears_waiting_and_releases(self):
        executor = TaskExecutor(max_concurrent=4, timeout=10)
        results = await asyncio.gather(
            *[executor.submit(error_task(0)) for _ in range(8)],
            return_exceptions=True)
        self.assertEqual(sum(isinstance(r, RuntimeError) for r in results), 8)

        self.assertEqual(executor.waiting_count, 0)
        self.assertEqual(executor.in_flight_count, 0)
        self.assertEqual(executor.pool.allocated, executor.pool.released)

    async def test_cancel_timeout_exception_together(self):
        """取消、超时、异常三类任务同时跑，并在外层统一取消。"""
        for _ in range(50):
            executor = TaskExecutor(max_concurrent=6, timeout=0.03)
            h_normal = asyncio.ensure_future(executor.submit(sleep_task(10)))
            h_swallow = asyncio.ensure_future(
                executor.submit(SwallowCancellation(0.001, swallow=3)()))
            h_error = asyncio.ensure_future(consume(
                executor.submit(error_task(0.001))))
            await asyncio.sleep(0.03)  # 等到超时触发
            h_normal.cancel()
            h_swallow.cancel()
            await asyncio.gather(h_normal, h_swallow, h_error,
                                 return_exceptions=True)

            self.assertEqual(executor.pool.allocated, executor.pool.released)
            self.assertEqual(executor.pool.in_flight, 0)
            self.assertEqual(executor.waiting_count, 0)
            self.assertEqual(executor.in_flight_count, 0)

    async def test_resource_count_invariant_under_load(self):
        """核心不变量：申请数 - 释放数 == 在途任务数，任意时刻成立。"""
        executor = TaskExecutor(max_concurrent=8, timeout=0.05)
        handles = []
        for index in range(400):
            kind = index % 4
            if kind == 0:
                coro = sleep_task(0.02)
            elif kind == 1:
                coro = error_task(0.005)
            elif kind == 2:
                coro = SwallowCancellation(0.001, swallow=2)()
            else:
                coro = sleep_task(0.5)  # 稍后会被取消
            handles.append(asyncio.ensure_future(
                consume(executor.submit(coro), cancel=(kind == 3))))
            await asyncio.sleep(0)

        # 运行过程中反复采样断言不变量
        for _ in range(10):
            await asyncio.sleep(0.005)
            pool = executor.pool
            self.assertEqual(pool.allocated - pool.released, pool.in_flight)
            self.assertEqual(pool.in_flight, executor.in_flight_count)
            self.assertGreaterEqual(executor.waiting_count,
                                    executor.in_flight_count)

        await asyncio.gather(*handles, return_exceptions=True)
        await asyncio.sleep(0.05)

        self.assertEqual(executor.pool.allocated, executor.pool.released)
        self.assertEqual(executor.pool.in_flight, 0)
        self.assertEqual(executor.in_flight_count, 0)
        self.assertEqual(executor.waiting_count, 0)

    async def test_double_release_is_safe_at_pool_level(self):
        pool = ResourcePool()
        resource = pool.acquire()
        self.assertTrue(pool.release(resource))
        for _ in range(5):
            self.assertFalse(pool.release(resource))  # 重复释放安全
        self.assertEqual((pool.allocated, pool.released), (1, 1))
        self.assertTrue(all(not _fd_open(fd) for fd in resource["fds"]))

    async def test_double_finalize_is_safe(self):
        """模拟超时/取消竞争下清理路径被执行两次：无错误状态。"""
        executor = TaskExecutor(max_concurrent=1, timeout=10)
        await executor._semaphore.acquire()
        task = object()
        executor._waiting.add(task)
        executor._inflight.add(task)
        resource = executor.pool.acquire()

        executor._finalize(task, resource)
        executor._finalize(task, resource)  # 二次清理必须安全
        executor._semaphore.release()

        self.assertEqual(executor.pool.allocated, 1)
        self.assertEqual(executor.pool.released, 1)
        self.assertEqual(executor.waiting_count, 0)
        self.assertEqual(executor.in_flight_count, 0)

    async def test_handle_count_stable(self):
        """5000 次混合任务后进程句柄数回落，无长期增长。"""
        executor = TaskExecutor(max_concurrent=16, timeout=0.05)
        gc.collect()
        start_fds = open_fd_count()
        for batch in range(50):
            handles = []
            to_cancel = []
            for index in range(100):
                kind = (batch + index) % 3
                if kind == 0:
                    coro = sleep_task(0.001)
                elif kind == 1:
                    coro = error_task(0)
                else:
                    coro = sleep_task(10)
                handle = asyncio.ensure_future(executor.submit(coro))
                if kind == 2:
                    to_cancel.append(handle)
                handles.append(handle)
            await asyncio.sleep(0)  # 让 submit 先启动，再取消
            for handle in to_cancel:
                handle.cancel()
            await asyncio.gather(*handles, return_exceptions=True)
        gc.collect()
        end_fds = open_fd_count()
        self.assertLessEqual(end_fds - start_fds, 0)
        self.assertEqual(executor.pool.allocated, executor.pool.released)
        self.assertEqual(executor.waiting_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
