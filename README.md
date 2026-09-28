# GSB-26 — Skip List (Python 3, stdlib only)

A thread-safe ordered key-value index with reproducible randomized
levels, O(log n) rank queries, and snapshot-isolated range scans.

## Files

- `skiplist.py` — the library (`SkipList`)
- `test_skiplist.py` — unit / concurrency / memory / reproducibility tests
- `benchmark.py` — search-step and memory measurements vs theory

## API

```python
from skiplist import SkipList
import random

sl = SkipList(p=0.5, max_level=16, rng=random.Random(42))
sl.insert(key, value)      # -> True if new key, else overwrite
sl.find(key[, default])    # -> value or default
sl.delete(key)             # -> True if existed
sl.rank(key)               # -> 1-based rank in key order, or None
sl.select(k)               # -> k-th (key, value) pair (1-based), or None
sl.range_scan(lo, hi)      # -> SnapshotIterator over lo <= k <= hi
sl.items()                 # all pairs, sorted
len(sl)
```

`rank` / `select` are order-statistics queries over an indexed skip
list (every pointer carries a width = number of bottom-level edges it
spans). They are positionally consistent with an ordered scan:
`select(k) == sl.items()[k-1]` and `rank(key)` is the 1-based position
of `key` in `sl.items()` (verified by `TestRankQueries`).

`range_scan` returns a `SnapshotIterator`: the matching pairs are
captured atomically (under the writer lock) at call time. Iterating it
never touches the live structure, so inserts/deletes during a scan
neither skip keys that were live at snapshot time nor return any record
twice; keys inserted after the snapshot never appear, keys deleted
after it still do. The iterator is single-pass; `to_list()` returns the
full snapshot buffer without consuming it, `len()` gives the snapshot
size, `remaining()` the unconsumed count. The snapshot holds plain
(key, value) pairs — not node references — so it never delays node
reclamation (verified by `TestSnapshotIterator`).

Level generation takes an injected random source (`rng=random.Random(seed)`):
same seed + same insertion sequence => identical node-level sequence
(verified by `TestReproducibility`). A custom `level_source=lambda: L`
replaces the geometric generator (used to force all-level-1 / all-max-level
extreme layouts in tests).

## Concurrency model

- One writer at a time (internal lock); readers are lock-free.
- Inserts fully initialize a node before linking it (atomic pointer stores
  under the GIL); deletes mark `deleted` before unlinking, and never mutate
  an unlinked node's `next` pointers — readers therefore never observe a
  half-updated node.
- `find` / `items` stay lock-free. `rank` / `select` and the snapshot
  capture in `range_scan` run under the writer lock, so they always
  observe a consistent structure (widths and links never torn).
- Snapshot iterators are consumed independently of the structure:
  concurrent write pressure cannot cause skips, duplicates, or
  half-updated records (asserted by
  `TestSnapshotIterator.test_snapshot_under_write_pressure` and
  `test_rank_select_under_write_pressure`).

## Run

```bash
python3 test_skiplist.py -v     # all self-tests
python3 benchmark.py            # steps + memory data
```

## Measured data (n = 100,000 keys, 20,000 queries x 5 seeds)

| p    | find steps | find [min,max] | rank steps | select steps | theory (1/p)*log_{1/p}(n) | avg level | theory 1/(1-p) | bytes/key |
|------|-----------|----------------|------------|--------------|---------------------------|-----------|----------------|-----------|
| 0.50 | 33.14     | [30.4, 36.3]   | 33.16      | 33.49        | 33.2                      | 1.997     | 2.000          | 216.0     |
| 0.25 | 33.48     | [27.4, 38.8]   | 33.52      | 32.61        | 33.2                      | 1.333     | 1.333          | 205.3     |
| 0.10 | 47.86     | [39.5, 60.9]   | 47.89      | 44.38        | 50.0                      | 1.111     | 1.111          | 201.8     |

- Expected search cost `(1/p)*log_{1/p}(n)` matches measurement; p=0.5 and
  p=0.25 cost about the same steps, p=0.1 ~50% more.
- `rank`/`select` descend the same tower path as `find`, so their step
  counts track the same expectation — order-statistics queries cost the
  same as a plain lookup.
- Memory: pointers per node = `1/(1-p)`. Smaller p means less index memory
  but longer searches and higher per-structure variance across seeds
  (fewer, thicker top towers). p=0.25 is a good compromise.
- bytes/key counts `_Node` + its `next` and `width` lists (excludes
  key/value objects); the width list adds one slot per level, scaling
  with the same `1/(1-p)` factor. CPython object overhead dominates the
  absolute number.

## Node recycling evidence

`TestNodeRecycling` performs 100,000 insert+delete rounds (plus a
100k-insert-then-delete-all phase) and counts live `_Node` objects via
`gc.get_objects()`:

```
[recycling] baseline=1 peak=100001 after=1 (rounds=100000)
```

Node count returns to the baseline (the head sentinel) after deletion —
deleted nodes are unlinked and reclaimed by the GC.
