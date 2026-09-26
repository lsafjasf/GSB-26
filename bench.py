"""Benchmarks: memory (compressed radix tree vs per-key storage) and
insert/lookup/prefix-query throughput on keys with heavy shared prefixes."""

import gc
import sys
import time

from radix_tree import RadixTree

VALUE = b"v" * 8


def gen_keys(n):
    """Object-store-like keys: long shared prefixes, unique suffixes."""
    for i in range(n):
        yield (
            b"/storage/v1/tenant-%04d/bucket-%02d/objects/%08d.data"
            % (i // 50000, (i // 1000) % 50, i)
        )


def build_radix(n):
    tree = RadixTree()
    for k in gen_keys(n):
        tree.insert(k, VALUE)
    return tree


def build_dict(n):
    return {k: VALUE for k in gen_keys(n)}


def build_sorted_list(n):
    return sorted(gen_keys(n))


def dict_bytes(d):
    total = sys.getsizeof(d)
    for k, v in d.items():
        total += sys.getsizeof(k)
    return total + sys.getsizeof(next(iter(d.values())))


def list_bytes(xs):
    return sys.getsizeof(xs) + sum(sys.getsizeof(k) for k in xs)


def bench_memory(n):
    gc.collect()
    tree = build_radix(n)
    naive_dict = build_dict(n)
    naive_list = build_sorted_list(n)
    gc.collect()

    t_bytes = tree.memory_bytes()
    d_bytes = dict_bytes(naive_dict)
    l_bytes = list_bytes(naive_list)
    stats = tree.stats()
    raw_key_bytes = sum(sys.getsizeof(k) for k in naive_list)
    logical_key_bytes = sum(len(k) for k in naive_list)

    print("== Memory (n = %d keys, avg key len %.1f B) =="
          % (n, logical_key_bytes / n))
    print("radix tree (memory_bytes)   : %10.1f KiB" % (t_bytes / 1024))
    print("naive dict  (full keys)     : %10.1f KiB" % (d_bytes / 1024))
    print("naive sorted list (full keys): %9.1f KiB" % (l_bytes / 1024))
    print("saving vs dict              : %10.1f%%" % (100 * (1 - t_bytes / d_bytes)))
    print("saving vs sorted list       : %10.1f%%" % (100 * (1 - t_bytes / l_bytes)))
    print("logical: %d live label B in arena of %d B vs %d B raw keys "
          "(%.1f%%); %d nodes / %d edges"
          % (stats["label_bytes"], stats["arena_bytes"], logical_key_bytes,
             100 * stats["label_bytes"] / logical_key_bytes,
             stats["nodes"], stats["edges"]))
    return tree, naive_dict


def bench_throughput(n, tree, naive_dict):
    print("\n== Throughput (n = %d) ==" % n)

    t0 = time.perf_counter()
    fresh = build_radix(n)
    dt = time.perf_counter() - t0
    print("insert (radix)   : %10.0f keys/s  (%.3f s)" % (n / dt, dt))

    t0 = time.perf_counter()
    d = {}
    for k in gen_keys(n):
        d[k] = VALUE
    dt = time.perf_counter() - t0
    print("insert (dict)    : %10.0f keys/s  (%.3f s)  [baseline]" % (n / dt, dt))

    probe = list(gen_keys(n))[:: max(1, n // 100000)]
    m = len(probe)

    t0 = time.perf_counter()
    for k in probe:
        tree.get(k)
    dt = time.perf_counter() - t0
    print("lookup (radix)   : %10.0f keys/s  (%.3f s for %d)" % (m / dt, dt, m))

    t0 = time.perf_counter()
    for k in probe:
        d.get(k)
    dt = time.perf_counter() - t0
    print("lookup (dict)    : %10.0f keys/s  (%.3f s for %d)  [baseline]"
          % (m / dt, dt, m))

    prefixes = [b"/storage/v1/tenant-%04d/" % t for t in range(4)]
    t0 = time.perf_counter()
    hits = sum(1 for p in prefixes for _ in tree.items_with_prefix(p))
    dt = time.perf_counter() - t0
    print("prefix query     : %d prefixes -> %d results in %.3f s (%.0f results/s)"
          % (len(prefixes), hits, dt, hits / dt))

    t0 = time.perf_counter()
    total = sum(1 for _ in tree.items())
    dt = time.perf_counter() - t0
    print("full scan (radix): %10.0f keys/s  (%.3f s for %d)" % (total / dt, dt, total))

    t0 = time.perf_counter()
    total = sum(1 for _ in sorted(naive_dict.items()))
    dt = time.perf_counter() - t0
    print("full scan (dict+sort): %6.0f keys/s  (%.3f s for %d)  [baseline]"
          % (total / dt, dt, total))

    victims = list(gen_keys(n))[::2]
    t0 = time.perf_counter()
    for k in victims:
        fresh.delete(k)
    dt = time.perf_counter() - t0
    print("delete (radix)   : %10.0f keys/s  (%.3f s for %d)"
          % (len(victims) / dt, dt, len(victims)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    tree, naive_dict = bench_memory(n)
    bench_throughput(n, tree, naive_dict)


if __name__ == "__main__":
    main()
