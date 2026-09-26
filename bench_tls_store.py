"""Throughput & latency benchmark for tls_store (stdlib only)."""

import statistics
import threading
import time

from tls_store import CounterStore

OPS_PER_THREAD = 200_000
LAT_SAMPLES = 50_000


def bench_throughput(n_threads: int) -> float:
    store = CounterStore(start_reaper=False)
    barrier = threading.Barrier(n_threads + 1)

    def worker():
        barrier.wait()
        add = store.add
        for i in range(OPS_PER_THREAD):
            add("k", 1)
        store.close_local()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    barrier.wait()
    start = time.perf_counter()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    store.flush_all()
    expected = n_threads * OPS_PER_THREAD
    assert store.total() == expected, (store.total(), expected)
    store.close()
    return expected / elapsed


def bench_write_latency() -> None:
    store = CounterStore(start_reaper=False)
    add = store.add
    samples = []
    for i in range(LAT_SAMPLES):
        t0 = time.perf_counter_ns()
        add("k", 1)
        samples.append(time.perf_counter_ns() - t0)
    store.close()
    samples.sort()
    avg = statistics.fmean(samples)
    p50 = samples[len(samples) // 2]
    p99 = samples[int(len(samples) * 0.99)]
    print(f"  write add() latency: avg={avg:,.0f} ns  "
          f"p50={p50:,} ns  p99={p99:,} ns  (n={LAT_SAMPLES:,})")


def bench_read_latency_under_load(n_writers: int = 4) -> None:
    store = CounterStore(start_reaper=False)
    stop = threading.Event()

    def worker():
        add = store.add
        while not stop.is_set():
            add("k", 1)

    threads = [threading.Thread(target=worker) for _ in range(n_writers)]
    for t in threads:
        t.start()
    samples = []
    deadline = time.perf_counter() + 2.0
    i = 0
    while time.perf_counter() < deadline:
        if i % 100 == 0:
            store.flush_all()
        i += 1
        t0 = time.perf_counter_ns()
        store.snapshot()
        samples.append(time.perf_counter_ns() - t0)
    stop.set()
    for t in threads:
        t.join()
    store.close()
    samples.sort()
    avg = statistics.fmean(samples)
    p50 = samples[len(samples) // 2]
    p99 = samples[int(len(samples) * 0.99)]
    print(f"  snapshot() latency under {n_writers} writers: "
          f"avg={avg:,.0f} ns  p50={p50:,} ns  p99={p99:,} ns  "
          f"(n={len(samples):,})")


def main() -> None:
    print(f"ops per thread: {OPS_PER_THREAD:,}")
    print("[throughput]")
    for n in (1, 2, 4, 8):
        ops = bench_throughput(n)
        print(f"  threads={n}: {ops / 1e6:,.2f} M ops/s")
    print("[latency]")
    bench_write_latency()
    bench_read_latency_under_load()


if __name__ == "__main__":
    main()
