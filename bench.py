"""Benchmarks for versioned_store. Run: python3 bench.py

Experiment A: space usage vs. number of retained versions.
Experiment B: 1,000,000 updates, 100+ retained versions -> query latency
              and memory footprint.
"""

import random
import sys
import time
import tracemalloc

from versioned_store import VersionedStore, _Node

NODE_BYTES = sys.getsizeof(_Node(0, 0, None, None))
VALUE_BYTES = 28  # approx. size of a small int value; keys are ints too


def fmt_mb(nbytes):
    return "%.1f MB" % (nbytes / 1e6)


def pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def experiment_space():
    print("=" * 78)
    print("Experiment A: space growth vs. retained versions")
    print("  base load: 100,000 keys; then 100 versions x 100 random updates each")
    print("  (full-copy baseline = versions x full tree)")
    print("=" * 78)
    rng = random.Random(42)
    keyspace = 200_000
    store = VersionedStore()
    store.commit({k: k for k in range(100_000)})
    header = "%10s %14s %14s %16s %16s" % (
        "versions", "live nodes", "est. memory", "full-copy est.", "nodes created")
    print(header)
    print("-" * len(header))
    for step in range(101):
        if step > 0:
            batch = {rng.randrange(keyspace): step for _ in range(100)}
            store.commit(batch)
        if step % 10 == 0:
            live = store.live_node_count()
            est = live * NODE_BYTES
            full = (step + 1) * 100_000 * NODE_BYTES
            recent = store.info()["nodes_created"]
            print("%10d %14d %14s %16s %16d" % (step + 1, live, fmt_mb(est),
                                                fmt_mb(full), recent))
    print("node size: %d bytes (sys.getsizeof)" % NODE_BYTES)
    print()


def experiment_perf():
    print("=" * 78)
    print("Experiment B: 1,000,000 updates, 100+ retained versions")
    print("  phase 1: 99 versions x 10,000 updates (sliding-window gc, keep 5)")
    print("  phase 2: 100 versions x 100 updates (all retained)")
    print("=" * 78)
    rng = random.Random(2026)
    keyspace = 300_000
    store = VersionedStore()
    keys = list(range(keyspace))

    tracemalloc.start()
    t0 = time.perf_counter()
    # phase 1: bulk of the million updates, old versions reclaimed as we go
    for v in range(99):
        batch = {k: rng.randrange(1 << 30) for k in rng.sample(keys, 10_000)}
        store.commit(batch)
        if store.version_count() > 5:
            store.gc(store.latest_version - 4)
    t1 = time.perf_counter()
    # phase 2: 100 more versions, all retained
    for v in range(100):
        batch = {k: rng.randrange(1 << 30) for k in rng.sample(keys, 100)}
        store.commit(batch)
    t2 = time.perf_counter()

    total_updates = store.total_updates
    n_versions = store.version_count()
    print("total updates committed : %d" % total_updates)
    print("versions retained       : %d" % n_versions)
    print("commit throughput       : %.0f updates/s (phase 1), %.0f updates/s (phase 2)"
          % (990_000 / (t1 - t0), 10_000 / (t2 - t1)))

    # ---- memory ----
    live = store.live_node_count()
    est = live * NODE_BYTES
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    full_copy = n_versions * live * NODE_BYTES  # lower bound: every version a full tree
    print("live tree nodes         : %d (%s)" % (live, fmt_mb(est)))
    print("tracemalloc current/peak: %s / %s" % (fmt_mb(current), fmt_mb(peak)))
    print("full-copy would need    : >= %s (%d x)" % (fmt_mb(full_copy), n_versions))
    per_ver = [store.info(v)["nodes_created"] for v in store.version_ids()]
    print("new nodes per version   : min %d, median %d, max %d (tree has ~%d keys, log2 ~%d)"
          % (min(per_ver), sorted(per_ver)[len(per_ver) // 2], max(per_ver),
             keyspace, keyspace.bit_length()))

    # ---- query latency: point reads at random retained versions ----
    vids = store.version_ids()
    n_gets = 20_000
    lat = []
    for _ in range(n_gets):
        vid = vids[rng.randrange(len(vids))]
        key = rng.randrange(keyspace)
        t = time.perf_counter_ns()
        try:
            store.get(key, version=vid)
        except KeyError:
            pass
        lat.append(time.perf_counter_ns() - t)
    lat.sort()
    print("get()  x%d              : avg %.1f us, p50 %.1f us, p99 %.1f us"
          % (n_gets, sum(lat) / len(lat) / 1e3, pct(lat, 0.50) / 1e3, pct(lat, 0.99) / 1e3))

    # ---- query latency: range scans of 1000 keys at random versions ----
    n_scans = 300
    lat = []
    for _ in range(n_scans):
        vid = vids[rng.randrange(len(vids))]
        start = rng.randrange(keyspace)
        t = time.perf_counter_ns()
        list(store.scan(version=vid, start=start, limit=1000))
        lat.append(time.perf_counter_ns() - t)
    lat.sort()
    print("scan(1000) x%d          : avg %.1f us, p50 %.1f us, p99 %.1f us"
          % (n_scans, sum(lat) / len(lat) / 1e3, pct(lat, 0.50) / 1e3, pct(lat, 0.99) / 1e3))

    # ---- historical vs latest version read cost ----
    oldest, latest = vids[0], vids[-1]
    for name, vid in (("oldest retained", oldest), ("latest", latest)):
        t = time.perf_counter_ns()
        for _ in range(2000):
            try:
                store.get(rng.randrange(keyspace), version=vid)
            except KeyError:
                pass
        print("get() @ %-15s v%-4d : %.1f us/op"
              % (name, vid, (time.perf_counter_ns() - t) / 2000 / 1e3))


if __name__ == "__main__":
    experiment_space()
    experiment_perf()
