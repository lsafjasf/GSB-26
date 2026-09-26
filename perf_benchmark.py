"""Performance benchmark at 100k records (stdlib only)."""

import random
import statistics
import time

from indexed_store import IndexedStore

N = 100_000
QUERIES = 1_000


def timed(fn, repeats=1):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


def main():
    rng = random.Random(7)
    store = IndexedStore(indexed_fields=("age", "city"))
    cities = ["bj", "sh", "gz", "sz", "hz", "cd", "wh", "nj"]

    # --- bulk insert ---
    t, _ = timed(lambda: [store.put(i, {
        "age": rng.randint(18, 80),
        "city": rng.choice(cities),
        "payload": i,
    }) for i in range(N)])
    print(f"insert {N:>7,} records (2 indexes) : {t:8.3f}s  "
          f"({N / t:>11,.0f} ops/s)")

    # --- point get ---
    t, _ = timed(lambda: [store.get(i) for i in range(0, N, 10)])
    print(f"get   {N // 10:>7,} by pk             : {t:8.4f}s  "
          f"({(N // 10) / t:>11,.0f} ops/s)")

    # --- equality queries (avg ~N/63 hits for age, ~N/8 for city) ---
    lat = []
    hits = 0
    for _ in range(QUERIES):
        age = rng.randint(18, 80)
        t, res = timed(lambda: store.query_eq("age", age))
        lat.append(t)
        hits += len(res)
    print(f"query_eq age   x{QUERIES} (avg {hits // QUERIES} hits): "
          f"mean {statistics.mean(lat) * 1e3:7.3f}ms  "
          f"p50 {statistics.median(lat) * 1e3:7.3f}ms")

    lat = []
    hits = 0
    for _ in range(QUERIES):
        city = rng.choice(cities)
        t, res = timed(lambda: store.query_eq("city", city))
        lat.append(t)
        hits += len(res)
    print(f"query_eq city  x{QUERIES} (avg {hits // QUERIES} hits): "
          f"mean {statistics.mean(lat) * 1e3:7.3f}ms  "
          f"p50 {statistics.median(lat) * 1e3:7.3f}ms")

    # --- range queries of varying width ---
    for width in (1, 10, 62):
        lat = []
        hits = 0
        for _ in range(QUERIES):
            lo = rng.randint(18, 80 - width)
            t, res = timed(lambda: store.query_range("age", lo, lo + width))
            lat.append(t)
            hits += len(res)
        print(f"query_range age width={width:>2} x{QUERIES} "
              f"(avg {hits // QUERIES} hits): "
              f"mean {statistics.mean(lat) * 1e3:7.3f}ms  "
              f"p50 {statistics.median(lat) * 1e3:7.3f}ms")

    # --- updates that migrate index keys ---
    pks = rng.sample(range(N), 10_000)
    t, _ = timed(lambda: [store.update(pk, age=rng.randint(18, 80))
                          for pk in pks])
    print(f"update {10_000:>6,} index-key migrations: {t:8.3f}s  "
          f"({10_000 / t:>11,.0f} ops/s)")

    # --- deletes ---
    t, _ = timed(lambda: store.delete_many(pks))
    print(f"delete {10_000:>6,} records (batch)    : {t:8.3f}s  "
          f"({10_000 / t:>11,.0f} ops/s)")
    assert len(store) == N - 10_000

    # --- full-table scan baseline for comparison ---
    t, res = timed(lambda: store.scan(field="age", lo=30, hi=40))
    print(f"scan baseline age in [30,40] (1x, {len(res)} hits): {t:8.4f}s")
    t, res = timed(lambda: store.query_range("age", 30, 40))
    print(f"query_range  age in [30,40] (1x, {len(res)} hits): {t:8.4f}s")


if __name__ == "__main__":
    main()
