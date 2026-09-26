"""Replay performance benchmark.

Usage: python3 bench.py [N]   (N = records written after the checkpoint,
default 200000). Total records written is 2N (N before + N after checkpoint).
"""

import shutil
import sys
import time

from wal import KVStore

DIR = "bench_data"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    shutil.rmtree(DIR, ignore_errors=True)

    t0 = time.perf_counter()
    store = KVStore.open(DIR, fsync=False)
    for i in range(n):
        store.set("key%08d" % i, i)
    store.checkpoint()
    for i in range(n):
        store.set("key%08d" % i, i + 1)
    store.close()
    t_write = time.perf_counter() - t0

    t0 = time.perf_counter()
    store = KVStore.open(DIR)
    t_replay = time.perf_counter() - t0
    assert len(store) == n, len(store)
    assert store.applied_lsn == 2 * n
    store.close()

    print("records written total      : %d (checkpoint after first %d)" % (2 * n, n))
    print("records replayed on restart: %d" % n)
    print("write+checkpoint time      : %.3f s (%.0f rec/s)" % (t_write, 2 * n / t_write))
    print("replay time                : %.3f s (%.0f rec/s)" % (t_replay, n / t_replay))
    shutil.rmtree(DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
