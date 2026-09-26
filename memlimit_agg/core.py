"""Memory-bounded group-by aggregation with disk spill and two-phase merge.

Design
------
Phase 1 (partial aggregation): records are streamed into an in-memory hash
table of partial aggregate states.  A running byte estimate is maintained;
once it exceeds the configured budget, the table is sorted by group key and
flushed to a spill file (a "run") on disk, and the table is reset.

Phase 2 (merge): all runs (including the final in-memory table, flushed as
one last run) are k-way merged with a heap.  Because every run is sorted by
group key, all partial states for one group are contiguous in the merge
stream, so only ONE group's state is live at a time while merging.  Final
results are produced as a stream, so even millions of groups never need to
be resident in memory at once.

Null / missing-value rules (also documented in README.md)
---------------------------------------------------------
* Group key None or NaN            -> the "missing" group, key None.
* Record missing the key field     -> treated as None -> missing group.
* Value None or NaN                -> ignored by sum/min/max/count_distinct.
* count                            -> counts RECORDS, regardless of values.
* sum of an all-None group         -> 0.
* min/max of an all-None group     -> None.
* count_distinct                   -> exact; None/NaN values not counted.
* Duplicate records                -> counted/aggregated once per occurrence
                                      (they are real records; only
                                      count_distinct de-duplicates values).
"""

import heapq
import itertools
import os
import pickle
import tempfile

OPS = ("count", "sum", "min", "max", "count_distinct")


def _is_nan(value):
    return isinstance(value, float) and value != value


def normalize_key(key):
    """Canonicalize a group key: None/NaN -> missing (None); tuples element-wise."""
    if key is None or _is_nan(key):
        return None
    if isinstance(key, tuple):
        return tuple(normalize_key(k) for k in key)
    return key


def _sort_key(key):
    """Total ordering across heterogeneous key types; missing (None) sorts first."""
    if key is None:
        return (0, "", 0.0, "")
    if isinstance(key, bool):
        return (1, "", float(key), "")
    if isinstance(key, (int, float)):
        return (1, "", float(key), "")
    if isinstance(key, str):
        return (2, "", 0.0, key)
    if isinstance(key, tuple):
        return (3, "", 0.0, tuple(_sort_key(k) for k in key))
    return (4, type(key).__name__, 0.0, repr(key))


def _est_key(key):
    """Rough byte estimate for one group key."""
    if key is None:
        return 8
    if isinstance(key, str):
        return 49 + len(key)
    if isinstance(key, tuple):
        return 56 + sum(_est_key(k) for k in key)
    return 32


def _init_state(op):
    if op == "count":
        return 0
    if op == "sum":
        return 0
    if op in ("min", "max"):
        return None
    if op == "count_distinct":
        return set()
    raise ValueError("unknown op: %r" % (op,))


def _update_state(op, state, value):
    # value is guaranteed not None/NaN here (filtered by caller); `count`
    # is handled by the caller as well.
    if op == "sum":
        return state + value
    if op == "min":
        return value if state is None or value < state else state
    if op == "max":
        return value if state is None or value > state else state
    if op == "count_distinct":
        state.add(value)
        return state
    raise ValueError("unknown op: %r" % (op,))


def _merge_states(op, a, b):
    if op in ("count", "sum"):
        return a + b
    if op == "min":
        if a is None:
            return b
        if b is None:
            return a
        return a if a <= b else b
    if op == "max":
        if a is None:
            return b
        if b is None:
            return a
        return a if a >= b else b
    if op == "count_distinct":
        return a | b
    raise ValueError("unknown op: %r" % (op,))


def _finalize(op, state):
    if op == "count_distinct":
        return len(state)
    return state


class GroupByAggregator:
    """Streaming group-by aggregator with a configurable memory budget.

    Parameters
    ----------
    key : str | callable
        Field name of the group key in each record dict, or a callable
        record -> key.
    aggs : dict
        Mapping op -> field name (or callable record -> value, or None).
        Supported ops: count, sum, min, max, count_distinct.
        `count` ignores its field and counts records.
    memory_budget : int
        Byte budget for the in-memory partial-aggregate table.  When the
        estimated size exceeds it, the table spills to disk.
    spill_dir : str | None
        Directory for spill files (default: system temp dir).
    """

    def __init__(self, key, aggs, memory_budget=64 << 20, spill_dir=None):
        unknown = set(aggs) - set(OPS)
        if unknown:
            raise ValueError("unknown ops: %r" % (sorted(unknown),))
        self._key_fn = key if callable(key) else (lambda rec, f=key: rec.get(f))
        self.aggs = dict(aggs)
        self._budget = int(memory_budget)
        self._spill_dir = spill_dir
        self._table = {}
        self._est = 0
        self._runs = []
        self.spill_count = 0
        self.spill_bytes = 0
        self.records_seen = 0

    # -- ingestion ------------------------------------------------------

    def add(self, record):
        self.records_seen += 1
        key = normalize_key(self._key_fn(record))
        state = self._table.get(key)
        if state is None:
            state = {op: _init_state(op) for op in self.aggs}
            self._table[key] = state
            self._est += _est_key(key) + 64 * len(self.aggs)
            if "count_distinct" in self.aggs:
                self._est += 216  # base size of the distinct-value set
        for op, field in self.aggs.items():
            if op == "count":
                state[op] += 1
                continue
            value = self._value_of(record, field)
            if value is None or _is_nan(value):
                continue  # missing values are ignored by value-based ops
            if op == "count_distinct":
                distinct = state[op]
                if value not in distinct:
                    distinct.add(value)
                    self._est += 72
            else:
                state[op] = _update_state(op, state[op], value)
        if self._est >= self._budget:
            self._spill()

    def add_many(self, records):
        for record in records:
            self.add(record)

    @staticmethod
    def _value_of(record, field):
        if field is None:
            return None
        if callable(field):
            return field(record)
        return record.get(field)

    # -- phase 1: spill -------------------------------------------------

    def _spill(self):
        if not self._table:
            return
        items = sorted(self._table.items(), key=lambda kv: _sort_key(kv[0]))
        fd, path = tempfile.mkstemp(prefix="agg_run_", suffix=".bin",
                                    dir=self._spill_dir)
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(len(items), fh, pickle.HIGHEST_PROTOCOL)
            for key, state in items:
                pickle.dump((key, state), fh, pickle.HIGHEST_PROTOCOL)
        self._runs.append(path)
        self.spill_count += 1
        self.spill_bytes += os.path.getsize(path)
        self._table = {}
        self._est = 0

    # -- phase 2: merge -------------------------------------------------

    @staticmethod
    def _iter_run(fh):
        try:
            count = pickle.load(fh)
            for _ in range(count):
                yield pickle.load(fh)
        except EOFError:
            return

    def _merged_items(self):
        """Yield (key, merged_state) in sorted-key order across all runs."""
        self._spill()  # flush whatever is left in memory as the last run
        files = []
        try:
            streams = []
            for path in self._runs:
                fh = open(path, "rb")
                files.append(fh)
                streams.append(self._iter_run(fh))
            merged = heapq.merge(*streams, key=lambda item: _sort_key(item[0]))
            for key, group in itertools.groupby(merged, key=lambda item: item[0]):
                acc = None
                for _, state in group:
                    if acc is None:
                        acc = state
                    else:
                        acc = {op: _merge_states(op, acc[op], state[op])
                               for op in self.aggs}
                yield key, acc
        finally:
            for fh in files:
                fh.close()
            for path in self._runs:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._runs = []

    # -- results --------------------------------------------------------

    def iter_results(self):
        """Yield (group_key, {op: value}) without materializing all groups.

        Missing group keys are yielded as None.
        """
        if self._runs or self._est >= self._budget:
            source = self._merged_items()
        else:
            items = sorted(self._table.items(), key=lambda kv: _sort_key(kv[0]))
            source = iter(items)
        for key, state in source:
            yield key, {op: _finalize(op, state[op]) for op in self.aggs}

    def result(self):
        """Materialize all results as {group_key: {op: value}}."""
        return dict(self.iter_results())
