"""压力测试：连续运行 100,000 次任务（含取消与异常），
验证句柄数与内存峰值保持平稳。

运行：python3 stress_test.py [任务总数，默认 100000]
"""

import asyncio
import gc
import os
import resource
import sys
import tracemalloc

from task_executor.executor import TaskExecutor

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
BATCH = 500
REPORT_EVERY = 10_000


def open_fd_count():
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


def rss_kb():
    with open(f"/proc/{os.getpid()}/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


async def sleep_task(delay):
    await asyncio.sleep(delay)
    return "ok"


async def error_task():
    await asyncio.sleep(0)
    raise RuntimeError("boom")


async def main():
    tracemalloc.start()
    gc.collect()
    await asyncio.sleep(0.02)

    executor = TaskExecutor(max_concurrent=32, timeout=0.05)
    base_fds = open_fd_count()
    base_rss = rss_kb()
    base_peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"基线: fds={base_fds} rss={base_rss}KB")
    print(f"{'已完成':>8} {'fds':>6} {'fd增量':>7} {'rss(KB)':>9} "
          f"{'rss增量(KB)':>12} {'py内存峰值(KB)':>14} {'在途':>5} {'等待':>5}")

    done = 0
    max_fds = base_fds
    max_rss = base_rss
    while done < TOTAL:
        handles = []
        to_cancel = []
        for index in range(BATCH):
            kind = (done + index) % 10
            if kind < 6:
                coro = sleep_task(0.001)          # 60% 正常
            elif kind < 8:
                coro = error_task()               # 20% 异常
            else:
                coro = sleep_task(10)             # 20% 将被取消
            handle = asyncio.ensure_future(executor.submit(coro))
            if kind >= 8:
                to_cancel.append(handle)
            handles.append(handle)
        await asyncio.sleep(0)
        for handle in to_cancel:
            handle.cancel()
        results = await asyncio.gather(*handles, return_exceptions=True)
        done += BATCH

        # 每批结束后断言不变量
        pool = executor.pool
        assert pool.allocated - pool.released == pool.in_flight
        assert pool.in_flight == executor.in_flight_count

        max_fds = max(max_fds, open_fd_count())
        max_rss = max(max_rss, rss_kb())
        if done % REPORT_EVERY == 0 or done >= TOTAL:
            _, py_peak = tracemalloc.get_traced_memory()
            print(f"{done:>8} {open_fd_count():>6} "
                  f"{open_fd_count() - base_fds:>+7} {rss_kb():>9} "
                  f"{rss_kb() - base_rss:>+12} {py_peak // 1024:>14} "
                  f"{executor.in_flight_count:>5} {executor.waiting_count:>5}")

    await asyncio.sleep(0.05)
    gc.collect()
    await asyncio.sleep(0.02)

    final_fds = open_fd_count()
    final_rss = rss_kb()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    py_current, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print()
    print(f"任务总数        : {done}")
    print(f"资源申请/释放   : {executor.pool.allocated} / {executor.pool.released}")
    print(f"结束后在途/等待 : {executor.in_flight_count} / {executor.waiting_count}")
    print(f"句柄数 基线/峰值/结束: {base_fds} / {max_fds} / {final_fds}")
    print(f"RSS(KB) 基线/峰值/结束: {base_rss} / {max_rss} / {final_rss}")
    print(f"进程峰值RSS(KB, ru_maxrss): {peak_rss} (基线 {base_peak_rss})")
    print(f"Python 内存 当前/峰值(KB, tracemalloc): "
          f"{py_current // 1024} / {py_peak // 1024}")

    ok = True
    checks = [
        ("申请数 == 释放数", executor.pool.allocated == executor.pool.released),
        ("在途任务 == 0", executor.in_flight_count == 0),
        ("等待队列 == 0", executor.waiting_count == 0),
        ("句柄无净增长", final_fds <= base_fds),
        ("句柄峰值有界(<=基线+并发*2+8)", max_fds <= base_fds + 32 * 2 + 8),
        ("RSS 增长有界(<=32MB)", final_rss - base_rss <= 32 * 1024),
    ]
    for name, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("== 压测通过 ==" if ok else "== 压测失败 ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
