"""Naive fully in-memory reference implementation.

Used as the ground truth for differential (dui-pai) tests and as the
memory/time baseline in benchmarks.  Shares the exact null-handling
helpers with the streaming implementation so semantics are identical
by construction; the tests verify the streaming/merge machinery itself.
"""

from .core import (_finalize, _init_state, _is_nan, _update_state,
                   normalize_key)


def reference_groupby(records, key, aggs):
    """records: iterable of dicts. Returns {group_key: {op: value}}."""
    key_fn = key if callable(key) else (lambda rec, f=key: rec.get(f))
    table = {}
    for rec in records:
        group_key = normalize_key(key_fn(rec))
        state = table.get(group_key)
        if state is None:
            state = {op: _init_state(op) for op in aggs}
            table[group_key] = state
        for op, field in aggs.items():
            if op == "count":
                state[op] += 1
                continue
            if field is None:
                continue
            value = field(rec) if callable(field) else rec.get(field)
            if value is None or _is_nan(value):
                continue
            state[op] = _update_state(op, state[op], value)
    return {k: {op: _finalize(op, state[op]) for op in state}
            for k, state in table.items()}


def reference_crosstab(records, row, col, op="sum", value=None):
    """Returns (cells, row_totals, col_totals)."""
    row_fn = row if callable(row) else (lambda rec, f=row: rec.get(f))
    col_fn = col if callable(col) else (lambda rec, f=col: rec.get(f))
    keyed = [{"k": (normalize_key(row_fn(r)), normalize_key(col_fn(r))),
              "v": (value(r) if callable(value) else r.get(value)) if value else None}
             for r in records]
    cells = reference_groupby(keyed, "k", {op: "v"})
    cells = {k: stats[op] for k, stats in cells.items()}
    row_totals, col_totals = {}, {}
    if op in ("sum", "count"):
        for (r, c), v in cells.items():
            row_totals[r] = row_totals.get(r, 0) + v
            col_totals[c] = col_totals.get(c, 0) + v
    return cells, row_totals, col_totals
