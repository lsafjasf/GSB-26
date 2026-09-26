"""In-memory primary table + ordered secondary indexes (stdlib only).

Design:
- Primary table: dict pk -> record (record is a dict of fields).
- Each indexed field keeps a sorted list of (index_value, pk) entries,
  maintained with bisect. Duplicates are allowed; entries with the same
  index value are ordered by pk, which makes query results deterministic
  and stable.
- Records whose indexed field is None (or missing) are NOT indexed:
  they never appear in equality/range queries on that field.
- Every mutation path (put that overwrites, put that changes an index
  key, delete, batch delete, clear) funnels through _index_remove /
  _index_add so the index cannot drift from the primary table.
"""

from bisect import bisect_left, bisect_right, insort

_MISSING = object()


class IndexedStore:
    def __init__(self, indexed_fields):
        self._table = {}
        self._indexed_fields = tuple(indexed_fields)
        # field -> sorted list of (value, pk)
        self._indexes = {f: [] for f in self._indexed_fields}

    # ---- internal index primitives -------------------------------------

    def _index_add(self, pk, record):
        for field in self._indexed_fields:
            value = record.get(field)
            if value is None:
                continue
            insort(self._indexes[field], (value, pk))

    def _index_remove(self, pk, record):
        for field in self._indexed_fields:
            value = record.get(field)
            if value is None:
                continue
            entries = self._indexes[field]
            pos = bisect_left(entries, (value, pk))
            # The entry must exist; if it does not, the index is corrupt.
            if pos >= len(entries) or entries[pos] != (value, pk):
                raise AssertionError(
                    f"index corrupt: ({value!r}, {pk!r}) missing from {field!r} index"
                )
            entries.pop(pos)

    # ---- primary table operations --------------------------------------

    def put(self, pk, record):
        """Insert or overwrite a record. Handles index-key migration."""
        old = self._table.get(pk, _MISSING)
        if old is not _MISSING:
            self._index_remove(pk, old)
        self._table[pk] = dict(record)
        self._index_add(pk, self._table[pk])

    def update(self, pk, **changes):
        """Partial update of an existing record."""
        if pk not in self._table:
            raise KeyError(pk)
        merged = dict(self._table[pk])
        merged.update(changes)
        self.put(pk, merged)

    def delete(self, pk):
        old = self._table.pop(pk, _MISSING)
        if old is _MISSING:
            return False
        self._index_remove(pk, old)
        return True

    def delete_many(self, pks):
        """Batch delete; returns number of records actually removed."""
        removed = 0
        for pk in pks:
            if self.delete(pk):
                removed += 1
        return removed

    def clear(self):
        self._table.clear()
        for entries in self._indexes.values():
            entries.clear()

    def get(self, pk, default=None):
        return self._table.get(pk, default)

    def __len__(self):
        return len(self._table)

    def __contains__(self, pk):
        return pk in self._table

    # ---- queries ---------------------------------------------------------

    def _check_field(self, field):
        if field not in self._indexes:
            raise KeyError(f"no index on field {field!r}")

    def query_eq(self, field, value):
        """Equality query; returns [(pk, record), ...] ordered by pk."""
        self._check_field(field)
        if value is None:
            return []
        entries = self._indexes[field]
        lo = bisect_left(entries, (value,))
        hi = bisect_right(entries, (value, _MaxSentinel))
        return [(pk, self._table[pk]) for _, pk in entries[lo:hi]]

    def query_range(self, field, lo=None, hi=None,
                    include_lo=True, include_hi=True):
        """Range query on an indexed field. None bound means unbounded.

        Returns [(pk, record), ...] ordered by (index value, pk).
        """
        self._check_field(field)
        entries = self._indexes[field]
        if lo is None:
            start = 0
        elif include_lo:
            start = bisect_left(entries, (lo,))
        else:
            start = bisect_right(entries, (lo, _MaxSentinel))
        if hi is None:
            end = len(entries)
        elif include_hi:
            end = bisect_right(entries, (hi, _MaxSentinel))
        else:
            end = bisect_left(entries, (hi,))
        return [(pk, self._table[pk]) for _, pk in entries[start:end]]

    def scan(self, field=None, value=None, lo=None, hi=None,
             include_lo=True, include_hi=True):
        """Full-table scan used as ground truth for differential testing."""
        out = []
        for pk, rec in self._table.items():
            if field is None:
                out.append((pk, rec))
                continue
            v = rec.get(field)
            if v is None:
                continue
            if value is not None and v != value:
                continue
            if lo is not None and (v < lo or (v == lo and not include_lo)):
                continue
            if hi is not None and (v > hi or (v == hi and not include_hi)):
                continue
            out.append((pk, rec))
        out.sort(key=lambda item: (item[1].get(field), item[0]))
        return out

    def index_entries(self, field):
        """Raw (value, pk) entries of an index, for invariant checks."""
        self._check_field(field)
        return list(self._indexes[field])


class _MaxSentinel:
    """Sorts after any real pk of the same value type."""

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False

    def __gt__(self, other):
        return True

    def __ge__(self, other):
        return True


_MaxSentinel = _MaxSentinel()
