"""有缺陷的任务执行器（仅作为复现现网问题的测试夹具，不是生产代码）。

四个分支与现网四类问题一一对应：
1. 外层取消分支：缓冲与句柄完全不释放（取消后缓冲泄漏）。
2. 超时分支：看门狗路径释放一次，异常处理路径再释放一次（二次释放，
   触发 EBADF，且 released 计数超过 allocated，进入错误状态）。
3. 异常分支：任务异常退出后不从等待队列移除条目（队列残留）。
4. 以上泄漏随任务数线性累积（长期运行句柄持续增长）。
"""

import asyncio
import os


class BuggyResourcePool:
    """每个任务申请一块 bytearray 缓冲和一对 os.pipe 句柄。

    release() 非幂等：重复调用会重复 close(fd) 并让计数失真。
    """

    def __init__(self):
        self.allocated = 0
        self.released = 0
        self.double_release_errors = []

    def acquire(self):
        read_fd, write_fd = os.pipe()
        os.set_inheritable(read_fd, False)
        os.set_inheritable(write_fd, False)
        self.allocated += 1
        return {"buffer": bytearray(4096), "fds": [read_fd, write_fd]}

    def release(self, resource):
        for fd in resource["fds"]:
            try:
                os.close(fd)
            except OSError as exc:  # 二次 close -> EBADF
                self.double_release_errors.append(exc)
        self.released += 1  # 非幂等：released 可能超过 allocated


class BuggyTaskExecutor:
    def __init__(self, max_concurrent=8, timeout=0.05):
        self.pool = BuggyResourcePool()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.waiting = set()  # 等待队列：已提交任务
        self.timeout = timeout

    async def submit(self, coro):
        task = asyncio.ensure_future(coro)
        self.waiting.add(task)
        await self.semaphore.acquire()
        resource = self.pool.acquire()
        loop = asyncio.get_running_loop()
        timed_out = False

        def _on_timeout():
            nonlocal timed_out
            timed_out = True
            self.pool.release(resource)  # 路径 A：看门狗释放
            task.cancel()

        watchdog = loop.call_later(self.timeout, _on_timeout)
        state = "running"
        try:
            return await task
        except asyncio.CancelledError:
            if timed_out:
                state = "timeout"
                # 缺陷 2：超时与取消同时发生，旧代码在这里又释放一次
                self.pool.release(resource)  # 路径 B：二次释放
                raise
            state = "outer_cancel"
            task.cancel()
            raise  # 缺陷 1：外层取消，资源不释放
        except Exception:
            state = "error"
            raise  # 缺陷 3：异常退出（见 finally，waiting 不清理）
        finally:
            if state in ("running", "error"):
                self.pool.release(resource)
            if state == "running":
                self.waiting.discard(task)  # 只有正常完成才出队
            watchdog.cancel()
            self.semaphore.release()
