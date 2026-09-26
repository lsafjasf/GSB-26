"""Differential fuzz test: IndexedStore vs full-table scan ground truth.

Randomly performs put / update / delete / delete_many, then after every
batch of ops verifies:
  1. Raw index entries == scan-derived entries (exact multiset + order).
  2. Random equality and range queries match scan results row by row.
"""

import random
import sys

from indexed_store import IndexedStore

FIELDS = ("age", "city")
AGES = [None, 18, 25, 30, 30, 30, 41, 55, 55, 70, 99]  # 30/55 weighted for dupes
CITIES = [None, "bj", "sh", "sh", "gz", "sz", "hz"]


def random_record(rng):
    return {"age": rng.choice(AGES), "city": rng.choice(CITIES),
            "payload": rng.randint(0, 10**6)}


def scan_entries(table, field):
    """Ground-truth index entries derived by scanning the primary table."""
    entries = [(rec[field], pk) for pk, rec in table.items()
               if rec.get(field) is not None]
    entries.sort()
    return entries


def check_invariants(store, table, rng, op_no):
    # 1. Primary table content matches the reference model.
    assert len(store) == len(table), f"op {op_no}: size mismatch"
    for pk, rec in table.items():
        assert store.get(pk) == rec, f"op {op_no}: record mismatch at pk={pk}"

    # 2. Raw index entries match scan-derived entries exactly (order too).
    for field in FIELDS:
        got = store.index_entries(field)
        want = scan_entries(table, field)
        assert got == want, (
            f"op {op_no}: index {field!r} drifted\n"
            f"  got  {got[:10]}... ({len(got)})\n"
            f"  want {want[:10]}... ({len(want)})")

    # 3. Random equality queries vs scan, row by row.
    for _ in range(20):
        field = rng.choice(FIELDS)
        value = rng.choice(AGES if field == "age" else CITIES)
        got = store.query_eq(field, value)
        want = store.scan(field=field, value=value) if value is not None else []
        assert [pk for pk, _ in got] == [pk for pk, _ in want], (
            f"op {op_no}: eq query {field}={value!r} mismatch")
        assert all(rec == table[pk] for pk, rec in got)

    # 4. Random range queries (all bound combinations) vs scan.
    for _ in range(20):
        field = rng.choice(FIELDS)
        domain = [a for a in AGES if a is not None] if field == "age" \
            else [c for c in CITIES if c is not None]
        lo, hi = sorted(rng.sample(domain, 2))
        ilo, ihi = rng.random() < 0.5, rng.random() < 0.5
        got = store.query_range(field, lo, hi, ilo, ihi)
        want = store.scan(field=field, lo=lo, hi=hi,
                          include_lo=ilo, include_hi=ihi)
        assert [pk for pk, _ in got] == [pk for pk, _ in want], (
            f"op {op_no}: range query {field} [{lo},{hi}] mismatch")
        assert all(rec == table[pk] for pk, rec in got)


def run_fuzz(seed, ops):
    rng = random.Random(seed)
    store = IndexedStore(indexed_fields=FIELDS)
    table = {}  # reference model
    live_pks = set()
    next_pk = 0

    for op_no in range(ops):
        roll = rng.random()
        if (roll < 0.60 and len(live_pks) < 2000) or not live_pks:  # insert
            rec = random_record(rng)
            store.put(next_pk, rec)
            table[next_pk] = rec
            live_pks.add(next_pk)
            next_pk += 1
        elif roll < 0.70:  # overwrite existing (may change index keys)
            pk = rng.choice(sorted(live_pks))
            rec = random_record(rng)
            store.put(pk, rec)
            table[pk] = rec
        elif roll < 0.82:  # partial update, often touching an index field
            pk = rng.choice(sorted(live_pks))
            changes = {}
            if rng.random() < 0.7:
                changes["age"] = rng.choice(AGES)
            if rng.random() < 0.7:
                changes["city"] = rng.choice(CITIES)
            if changes:
                store.update(pk, **changes)
                table[pk].update(changes)
        elif roll < 0.93:  # delete one
            pk = rng.choice(sorted(live_pks))
            assert store.delete(pk)
            del table[pk]
            live_pks.discard(pk)
        else:  # batch delete
            victims = rng.sample(sorted(live_pks),
                                 k=min(len(live_pks), rng.randint(1, 5)))
            removed = store.delete_many(victims)
            assert removed == len(victims)
            for pk in victims:
                del table[pk]
                live_pks.discard(pk)

        if op_no % 50 == 0 or op_no == ops - 1:
            check_invariants(store, table, rng, op_no)

    print(f"seed={seed}: {ops} ops OK, final size={len(store)}, "
          f"inserts seen={next_pk}")


if __name__ == "__main__":
    seeds = [int(a) for a in sys.argv[1:]] or [1, 2, 3, 42, 2026]
    for seed in seeds:
        run_fuzz(seed, ops=5000)
    print("ALL FUZZ RUNS PASSED")
