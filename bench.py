"""Benchmark: memory savings and throughput of RadixStore vs. a naive
per-key dict (uncompressed baseline).

Run:  python3 bench.py
"""

import gc
import random
import sys
import time
import tracemalloc

from packed_store import PackedRadixStore
from radix_store import RadixStore


def make_shared_prefix_keys(n, seed=7):
    """Object-storage-like keys with heavy prefix sharing."""
    rng = random.Random(seed)
    keys = []
    for _ in range(n):
        key = (b"bucket-7/tenant-%04d/region-%02d/objects/%08d.dat"
               % (rng.randrange(200), rng.randrange(8), rng.randrange(10**8)))
        keys.append(key)
    return keys


def make_long_keys(n, seed=11):
    """~200-byte path-like keys, deep shared directory prefixes."""
    rng = random.Random(seed)
    keys = []
    for _ in range(n):
        parts = [b"/data"]
        for depth in range(8):
            # few distinct names per level -> massive shared prefixes
            parts.append(b"dir-%03d" % rng.randrange(6))
        parts.append(b"file-%08d.bin" % rng.randrange(10**8))
        keys.append(b"/".join(parts))
    return keys


def make_binary_keys(n, seed=13):
    """Random binary keys (incl. \\x00) with a shared 16-byte prefix."""
    rng = random.Random(seed)
    shared = bytes(rng.randrange(256) for _ in range(16))
    return [shared + bytes(rng.randrange(256) for _ in range(16))
            for _ in range(n)]


def measure_memory(build):
    """Peak bytes allocated while ``build()`` runs, minus what it freed.

    Keys are (re)generated inside ``build`` so the naive baseline pays for
    its per-key bytes objects, while the compressed stores only retain
    their (smaller) internal representation.  ``gc.collect()`` first keeps
    transient garbage out of the reading.
    """
    gc.collect()
    tracemalloc.start()
    container = build()
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return container, current


def fmt_bytes(num):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return "%.1f %s" % (num, unit)
        num /= 1024


def memory_report(name, key_factory):
    keys = key_factory()
    value = b"v" * 8
    naive, naive_bytes = measure_memory(
        lambda: {k: value for k in key_factory()})
    store, radix_bytes = measure_memory(
        lambda: RadixStore((k, value) for k in key_factory()))
    packed, packed_bytes = measure_memory(
        lambda: PackedRadixStore.from_items((k, value) for k in key_factory()))
    st = store.stats()
    pst = packed.stats()
    raw_key_bytes = sum(len(k) for k in keys)
    print("### %s" % name)
    print("  keys: %d   avg key len: %.1f B   total raw key bytes: %s"
          % (len(keys), raw_key_bytes / len(keys), fmt_bytes(raw_key_bytes)))
    print("  naive dict (per-key storage)      : %10s  (1.00x)"
          % fmt_bytes(naive_bytes))
    print("  RadixStore (mutable, object nodes): %10s  (%.2fx vs naive)"
          % (fmt_bytes(radix_bytes), radix_bytes / naive_bytes))
    print("  PackedRadixStore (flat arrays)    : %10s  (%.2fx vs naive, "
          "%.0f%% saved)" % (fmt_bytes(packed_bytes),
                             packed_bytes / naive_bytes,
                             100.0 * (1 - packed_bytes / naive_bytes)))
    print("  key payload: raw %s -> compressed labels %s (%.1f%% of raw)"
          % (fmt_bytes(raw_key_bytes), fmt_bytes(st["label_bytes"]),
             100.0 * st["label_bytes"] / raw_key_bytes))
    print("  mutable tree: %d nodes / %d edges; packed structure buffers: %s"
          % (st["nodes"], st["edges"], fmt_bytes(pst["structure_bytes"])))
    # sanity: same content
    assert len(store) == len(naive)
    assert len(packed) == len(naive)
    assert list(store.keys()) == sorted(naive), "order mismatch"
    assert list(packed.keys()) == sorted(naive), "packed order mismatch"
    print()
    return keys


def throughput_report(keys):
    n = len(keys)
    value = b"v" * 8
    rng = random.Random(99)

    store = RadixStore()
    t0 = time.perf_counter()
    for k in keys:
        store.insert(k, value)
    t_insert = time.perf_counter() - t0

    hits = [rng.choice(keys) for _ in range(n)]
    t0 = time.perf_counter()
    for k in hits:
        store.get(k)
    t_hit = time.perf_counter() - t0

    misses = [k + b"!" for k in hits]
    t0 = time.perf_counter()
    for k in misses:
        store.get(k)
    t_miss = time.perf_counter() - t0

    # prefix queries: each matches ~n/200 keys on average
    prefixes = [k[:26] for k in rng.sample(keys, min(2000, n))]
    t0 = time.perf_counter()
    matched = 0
    for p in prefixes:
        matched += sum(1 for _ in store.keys_with_prefix(p))
    t_prefix = time.perf_counter() - t0

    t0 = time.perf_counter()
    total = sum(1 for _ in store.items())
    t_scan = time.perf_counter() - t0

    packed = PackedRadixStore.from_store(store)
    t0 = time.perf_counter()
    for k in hits:
        packed.get(k)
    t_packed_hit = time.perf_counter() - t0

    print("### Throughput (%d keys, Python %s)" % (n, sys.version.split()[0]))
    print("  insert:        %10.0f ops/s  (%d ops in %.2fs)"
          % (n / t_insert, n, t_insert))
    print("  lookup hit:    %10.0f ops/s  (%d ops in %.2fs)"
          % (n / t_hit, n, t_hit))
    print("  lookup miss:   %10.0f ops/s  (%d ops in %.2fs)"
          % (n / t_miss, n, t_miss))
    print("  prefix query:  %10.0f ops/s  (%d queries, %d matched keys)"
          % (len(prefixes) / t_prefix, len(prefixes), matched))
    print("  full scan:     %10.0f keys/s (%d keys in %.2fs)"
          % (total / t_scan, total, t_scan))
    print("  packed lookup: %10.0f ops/s  (%d ops in %.2fs)"
          % (n / t_packed_hit, n, t_packed_hit))
    print()


def main():
    print("=" * 72)
    print("MEMORY: prefix-compressed RadixStore vs naive per-key dict")
    print("=" * 72)
    shared_keys = memory_report(
        "shared object-store prefixes (100k keys)",
        lambda: make_shared_prefix_keys(100_000))
    memory_report("long ~200B path keys (50k keys)",
                  lambda: make_long_keys(50_000))
    memory_report("binary keys with \\x00, 16B shared prefix (100k keys)",
                  lambda: make_binary_keys(100_000))

    print("=" * 72)
    print("THROUGHPUT")
    print("=" * 72)
    throughput_report(shared_keys)


if __name__ == "__main__":
    main()
