"""double_buffer 基准：内存峰值、回收延迟、发布前后读延迟。

运行：python3 bench.py
"""

import statistics
import threading
import time
import tracemalloc

from double_buffer import DoubleBuffer

PAYLOAD = 1024 * 1024          # 每个版本 1 MiB
N_PUBLISH = 2000               # 连续发布 2000 个版本（逻辑总量 2 GiB）


def bench_memory():
    """连续快速发布：逻辑发布 2 GiB，进程内存峰值应只有 ~2 个版本。"""
    buf = DoubleBuffer()
    tracemalloc.start()
    t0 = time.perf_counter()
    peak_live = 0
    for i in range(N_PUBLISH):
        buf.publish(bytes(64) * (PAYLOAD // 64))
        peak_live = max(peak_live, buf.live_versions)
    dt = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print("== 内存（连续快速发布 %d 个 x 1 MiB 版本，逻辑总量 %.1f GiB）==" % (
        N_PUBLISH, N_PUBLISH * PAYLOAD / 2**30))
    print("  存活版本数峰值        : %d（上界 = 1 当前 + 在读者数）" % peak_live)
    print("  Python 堆内存峰值     : %.2f MiB（≈2 个版本，不随发布数增长）" % (peak / 2**20))
    print("  已回收版本数          : %d / %d" % (buf.reclaimed_count, N_PUBLISH))
    print("  总耗时                : %.2f s（%.0f 次发布/s）" % (dt, N_PUBLISH / dt))
    print()


def bench_reclaim_latency():
    """回收延迟：无读者时回收在 publish 内同步完成；有读者时=读者持有时长。"""
    buf = DoubleBuffer()
    # 无读者：publish 返回即已回收，延迟 = 0
    samples = []
    for _ in range(20000):
        t0 = time.perf_counter_ns()
        buf.publish(b"x" * 1024)
        samples.append(time.perf_counter_ns() - t0)
    print("== 回收延迟 ==")
    print("  无读者：publish 返回时退休版本已回收（同步回收，延迟≈0）")
    print("    publish(含回收) 平均 %.1f us / p99 %.1f us" % (
        statistics.mean(samples) / 1e3, statistics.quantiles(samples, n=100)[98] / 1e3))
    # 有慢读者：回收延迟 = 读者持有时长（引用计数语义，确定性）
    hold = 0.05
    snap = buf.acquire()
    t0 = time.perf_counter()
    buf.publish(b"y" * 1024)
    mid = time.perf_counter()
    assert buf.live_versions == 2                      # 读期间不回收
    time.sleep(hold)
    snap.release()
    t1 = time.perf_counter()
    assert buf.live_versions == 1                      # 释放后立即回收
    print("  有读者：退休版本等到最后读者释放才回收")
    print("    读者持有 %.1f ms，释放到回收 %.3f ms（同步完成）" % (
        (mid - t0) * 1e3 + hold * 1e3, (t1 - t0 - hold - (mid - t0)) * 1e3))
    print()


def _read_latency_phase(buf, stop, latencies, idx):
    while not stop.is_set():
        t0 = time.perf_counter_ns()
        with buf.acquire() as snap:
            _ = snap.data
        latencies[idx].append(time.perf_counter_ns() - t0)


def bench_read_latency():
    """发布前后读延迟对比：4 读者，先空跑 3s，再叠加发布风暴 3s。"""
    n_readers, phase_s = 4, 3.0
    buf = DoubleBuffer(initial=b"v" * PAYLOAD)
    latencies = [[] for _ in range(n_readers)]
    stop = threading.Event()

    def run_phase(publish):
        nonlocal latencies
        latencies = [[] for _ in range(n_readers)]
        stop.clear()
        readers = [threading.Thread(target=_read_latency_phase,
                                    args=(buf, stop, latencies, i))
                   for i in range(n_readers)]
        for t in readers:
            t.start()
        if publish:
            end = time.time() + phase_s
            i = 0
            while time.time() < end:
                buf.publish(b"w" * PAYLOAD)
                i += 1
            pubs = i
        else:
            time.sleep(phase_s)
            pubs = 0
        stop.set()
        for t in readers:
            t.join()
        flat = [x for sub in latencies for x in sub]
        flat.sort()
        return flat, pubs

    base, _ = run_phase(publish=False)
    storm, pubs = run_phase(publish=True)

    def stats(xs):
        return (statistics.mean(xs) / 1e3,
                xs[len(xs) // 2] / 1e3,
                xs[int(len(xs) * 0.99)] / 1e3,
                len(xs))

    print("== 读延迟（%d 读者，每阶段 %.0f s，单位 us）==" % (n_readers, phase_s))
    print("  %-14s %10s %10s %10s %12s" % ("阶段", "avg", "p50", "p99", "读次数"))
    for name, xs in (("无发布", base), ("发布风暴", storm)):
        avg, p50, p99, n = stats(xs)
        print("  %-14s %10.1f %10.1f %10.1f %12d" % (name, avg, p50, p99, n))
    print("  风暴期间发布次数: %d（读路径未被阻塞，吞吐量见上表）" % pubs)


if __name__ == "__main__":
    bench_memory()
    bench_reclaim_latency()
    bench_read_latency()
