"""复现脚本：在 BuggyTaskExecutor 上稳定复现四类资源泄漏问题。

运行：python3 reproduce_leaks.py
退出码：0 = 未发现问题；1 = 复现成功（即现网缺陷存在）。
"""

import asyncio
import contextlib
import gc
import os
import sys

from task_executor.executor_buggy import BuggyTaskExecutor


def open_fd_count():
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


async def sleep_task(delay):
    await asyncio.sleep(delay)
    return "ok"


async def error_task():
    await asyncio.sleep(0)
    raise RuntimeError("boom")


async def scenario_cancel_buffer_leak():
    """问题 1：取消后缓冲/句柄未释放。"""
    executor = BuggyTaskExecutor(max_concurrent=4, timeout=10)
    handles = [asyncio.ensure_future(
        executor.submit(sleep_task(10))) for _ in range(4)]
    await asyncio.sleep(0.02)
    for handle in handles:
        handle.cancel()
    await asyncio.gather(*handles, return_exceptions=True)
    leaked = executor.pool.allocated - executor.pool.released
    print(f"[问题1] 取消后泄漏: allocated={executor.pool.allocated} "
          f"released={executor.pool.released} 泄漏批次数={leaked}")
    return leaked > 0


async def scenario_timeout_cancel_double_release():
    """问题 2：超时与取消同时发生 -> 二次释放。"""
    executor = BuggyTaskExecutor(max_concurrent=2, timeout=0.02)
    handles = [asyncio.ensure_future(
        executor.submit(sleep_task(10))) for _ in range(2)]
    await asyncio.sleep(0.02)  # 与超时同时触发
    for handle in handles:
        handle.cancel()
    await asyncio.gather(*handles, return_exceptions=True)
    pool = executor.pool
    over_released = pool.released - pool.allocated
    badf = len(pool.double_release_errors)
    print(f"[问题2] 超时+取消: allocated={pool.allocated} "
          f"released={pool.released} (多释放 {over_released} 次), "
          f"EBADF 错误 {badf} 个")
    return over_released > 0 or badf > 0


async def scenario_exception_waiting_residue():
    """问题 3：任务异常退出后等待队列残留。"""
    executor = BuggyTaskExecutor(max_concurrent=4, timeout=10)
    results = await asyncio.gather(
        *[executor.submit(error_task()) for _ in range(8)],
        return_exceptions=True)
    assert sum(isinstance(r, RuntimeError) for r in results) == 8
    residue = len(executor.waiting)
    print(f"[问题3] 异常退出后等待队列残留条目数={residue}")
    return residue > 0


async def scenario_long_run_handle_growth():
    """问题 4：长期运行句柄数持续增长。"""
    gc.collect()
    await asyncio.sleep(0.02)
    baseline = open_fd_count()
    executor = BuggyTaskExecutor(max_concurrent=8, timeout=10)
    for round_no in range(5):
        handles = [asyncio.ensure_future(
            executor.submit(sleep_task(10))) for _ in range(8)]
        await asyncio.sleep(0.01)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)
        print(f"[问题4] 第 {round_no + 1} 轮后进程句柄数="
              f"{open_fd_count()} (基线 {baseline})")
    growth = open_fd_count() - baseline
    print(f"[问题4] 句柄净增长={growth}")
    return growth > 0


async def main():
    print("== 在有缺陷的执行器上复现四类问题 ==")
    results = [
        await scenario_cancel_buffer_leak(),
        await scenario_timeout_cancel_double_release(),
        await scenario_exception_waiting_residue(),
        await scenario_long_run_handle_growth(),
    ]
    reproduced = sum(results)
    print(f"== 复现结果: {reproduced}/4 类问题存在 ==")
    return 1 if reproduced else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
