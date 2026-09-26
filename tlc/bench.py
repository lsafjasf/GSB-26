"""Throughput & latency benchmark (stdlib only).

Run: python3 bench.py [ops_per_thread] [n_threads]
"""

import statistics
import sys
import threading
import time

from tlc_store import ThreadLocalMergeStore


def pct(samples, p):
    if not samples:
        return 0.0
    s = sorted(samples)
    return s[min(len(s) - 1, int(len(s) * p))]


def bench_writes(n_threads, ops, flush_every):
    """Local-write throughput with periodic merges; also samples
    flush latency and concurrent global-read latency."""
    s = ThreadLocalMergeStore()
    flush_lat = []
    read_lat = []
    lat_lock = threading.Lock()
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            t0 = time.perf_counter_ns()
            s.snapshot_with_total()
            dt = time.perf_counter_ns() - t0
            with lat_lock:
                read_lat.append(dt)

    def writer():
        local_flush = []
        for i in range(ops):
            s.add("bench")
            if flush_every and i % flush_every == 0 and i:
                t0 = time.perf_counter_ns()
                s.flush()
                local_flush.append(time.perf_counter_ns() - t0)
        s.close()
        with lat_lock:
            flush_lat.extend(local_flush)

    readers = [threading.Thread(target=reader) for _ in range(2)]
    t0 = time.perf_counter()
    for t in readers:
        t.start()
    workers = [threading.Thread(target=writer) for _ in range(n_threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    elapsed = time.perf_counter() - t0
    stop.set()
    for t in readers:
        t.join()

    total_ops = n_threads * ops
    assert s.total() == total_ops, f"lost/dup counts: {s.total()} != {total_ops}"
    print(f"[writes]   threads={n_threads:3d} ops/thread={ops:>8d} "
          f"flush_every={flush_every}")
    print(f"           throughput : {total_ops / elapsed:>12,.0f} local adds/s "
          f"(wall {elapsed:.3f}s, total merged = {s.total():,})")
    if flush_lat:
        print(f"           flush latency (ns): p50={pct(flush_lat, .50):.0f} "
              f"p99={pct(flush_lat, .99):.0f} p99.9={pct(flush_lat, .999):.0f} "
              f"max={max(flush_lat)}  (n={len(flush_lat)})")
    if read_lat:
        print(f"           global snapshot latency (ns): p50={pct(read_lat, .50):.0f} "
              f"p99={pct(read_lat, .99):.0f} p99.9={pct(read_lat, .999):.0f} "
              f"max={max(read_lat)}  (n={len(read_lat)})")
    print()


def bench_local_only():
    """Contention-free local write hot path (no flush at all)."""
    s = ThreadLocalMergeStore()
    ops = 2_000_000
    t0 = time.perf_counter()
    for _ in range(ops):
        s.add("x")
    el = time.perf_counter() - t0
    print(f"[local]    single thread, no merge: {ops / el:,.0f} adds/s "
          f"(pending={s.pending():,})")
    t0 = time.perf_counter()
    n = s.flush()
    print(f"           one flush of {n:,} pending adds: "
          f"{(time.perf_counter() - t0) * 1000:.3f} ms, global={s.total():,}")
    print()


def main():
    ops = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    nth = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print("=" * 78)
    bench_local_only()
    for threads in (1, 2, 4, nth if nth >= 4 else 8):
        for fe in (0, 1000, 100):
            bench_writes(threads, ops if threads <= 4 else ops // 2, fe)
    print("benchmark done: every run asserts final total == total local adds "
          "(no loss / no double count).")


if __name__ == "__main__":
    main()
