"""Batch schema expressed with fuzzgen combinators + structure checker.

Valid mode guarantees every business rule of sut.py (unique ids and the
sum<=2000 rule are enforced by a deterministic repair hook).
Violate mode keeps the exact same shape but lets leaf values break
business rules (oversized notes, negative ids, over-long lists, deeper
nesting via a larger max_depth budget).
"""

import fuzzgen as fg

meta = fg.Recursive(
    base=fg.Record({"note": fg.Text(min_len=0, max_len=64),
                    "sub": fg.Const(None)}),
    extend=lambda rec: fg.Record({"note": fg.Text(min_len=0, max_len=64),
                                  "sub": rec}),
    recurse_weight=0.6,
)

item = fg.Record({
    "id": fg.Int(0, 999999),
    "amount": fg.Int(1, 500),
    "tags": fg.ListOf(fg.Text(min_len=1, max_len=8), min_len=0, max_len=5),
    "meta": meta,
})


def _repair(rec, ctx):
    """Deterministically restore business invariants in valid mode."""
    if ctx.violate:
        return rec
    items = rec["items"]
    seen = set()
    for it in items:
        while it["id"] in seen:
            it["id"] += 1
        seen.add(it["id"])

    def total():
        return sum(i["amount"] for i in items)

    while total() > 2000:
        biggest = max(items, key=lambda i: i["amount"])
        biggest["amount"] = max(1, biggest["amount"] - (total() - 2000))
    return rec


BATCH = fg.Record({
    "batch_id": fg.Text(min_len=1, max_len=16),
    "items": fg.ListOf(item, min_len=1, max_len=8),
}, post=_repair)


# --- structure checker (shape/types only, no business rules) ---

def meta_shape(m):
    return (isinstance(m, dict)
            and set(m) == {"note", "sub"}
            and isinstance(m["note"], str)
            and (m["sub"] is None or meta_shape(m["sub"])))


def item_shape(it):
    def is_int(x):
        return isinstance(x, int) and not isinstance(x, bool)
    return (isinstance(it, dict)
            and set(it) == {"id", "amount", "tags", "meta"}
            and is_int(it["id"])
            and is_int(it["amount"])
            and isinstance(it["tags"], list)
            and all(isinstance(t, str) for t in it["tags"])
            and meta_shape(it["meta"]))


def batch_shape(b):
    return (isinstance(b, dict)
            and set(b) == {"batch_id", "items"}
            and isinstance(b["batch_id"], str)
            and isinstance(b["items"], list)
            and len(b["items"]) >= 1
            and all(item_shape(i) for i in b["items"]))
