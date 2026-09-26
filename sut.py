"""System under test: a nested 'transfer batch' processor.

Input: a JSON document
  {"batch_id": str, "items": [{"id": int, "amount": int,
                               "tags": [str], "meta": META}]}
  META := {"note": str, "sub": META | None}   (recursive)

Business rules (checked by validate):
  batch_id : 1..16 chars
  items    : 1..8 elements
  id       : int >= 0, unique across items
  amount   : int in 1..500, sum(amount) <= 2000
  tags     : <= 5 strings, each <= 8 chars
  meta     : note <= 64 chars, nesting depth <= 4

Branch coverage is recorded as symbolic tags in COVERAGE so experiments
can count how many distinct branches each input strategy reaches.
"""

import json

COVERAGE = set()

MAX_DEPTH = 4
MAX_NOTE = 64
MAX_TAG_LEN = 8
MAX_TAGS = 5
MAX_AMOUNT = 500
MAX_TOTAL = 2000
MAX_ITEMS = 8
MAX_BATCH_ID = 16


class Reject(Exception):
    """Input rejected by parsing, structural check, or business rules."""


def hit(tag):
    COVERAGE.add(tag)


def parse_input(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        hit("parse:not-bytes")
        raise Reject("not bytes")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        hit("parse:non-utf8")
        raise Reject("non-utf8")
    hit("parse:utf8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        hit("parse:bad-json")
        raise Reject("bad json")
    hit("parse:json")
    if not isinstance(data, dict):
        hit("parse:not-object")
        raise Reject("not object")
    hit("parse:object")
    return data


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _check_meta(meta, depth):
    hit("meta:visit")
    if depth > MAX_DEPTH:
        hit("meta:too-deep")
        raise Reject("meta too deep")
    if not isinstance(meta, dict):
        hit("meta:not-object")
        raise Reject("meta not object")
    note = meta.get("note")
    if not isinstance(note, str):
        hit("meta:note-not-str")
        raise Reject("note not str")
    if len(note) > MAX_NOTE:
        hit("meta:note-too-long")
        raise Reject("note too long")
    if len(note) == 0:
        hit("meta:note-empty")
    sub = meta.get("sub")
    if sub is None:
        hit("meta:leaf")
    else:
        hit("meta:recurse")
        _check_meta(sub, depth + 1)
    if depth >= 3:
        hit("meta:deep-ok")  # deep branch: needs >= 3 nesting levels


def validate(data):
    batch_id = data.get("batch_id")
    if not isinstance(batch_id, str):
        hit("batch:id-not-str")
        raise Reject("batch_id not str")
    if not batch_id:
        hit("batch:id-empty")
        raise Reject("batch_id empty")
    if len(batch_id) > MAX_BATCH_ID:
        hit("batch:id-too-long")
        raise Reject("batch_id too long")
    hit("batch:id-ok")

    items = data.get("items")
    if not isinstance(items, list):
        hit("items:not-list")
        raise Reject("items not list")
    if not items:
        hit("items:empty")
        raise Reject("items empty")
    if len(items) > MAX_ITEMS:
        hit("items:too-many")
        raise Reject("too many items")

    seen_ids = set()
    total = 0
    for it in items:
        if not isinstance(it, dict):
            hit("item:not-object")
            raise Reject("item not object")
        iid = it.get("id")
        if not _is_int(iid):
            hit("item:id-not-int")
            raise Reject("id not int")
        if iid < 0:
            hit("item:id-negative")
            raise Reject("id negative")
        if iid in seen_ids:
            hit("item:id-dup")
            raise Reject("id duplicate")
        seen_ids.add(iid)

        amount = it.get("amount")
        if not _is_int(amount):
            hit("item:amount-not-int")
            raise Reject("amount not int")
        if amount <= 0:
            hit("item:amount-nonpositive")
            raise Reject("amount nonpositive")
        if amount > MAX_AMOUNT:
            hit("item:amount-too-big")
            raise Reject("amount too big")
        total += amount

        tags = it.get("tags")
        if not isinstance(tags, list):
            hit("item:tags-not-list")
            raise Reject("tags not list")
        if len(tags) > MAX_TAGS:
            hit("item:too-many-tags")
            raise Reject("too many tags")
        for t in tags:
            if not isinstance(t, str):
                hit("item:tag-not-str")
                raise Reject("tag not str")
            if len(t) > MAX_TAG_LEN:
                hit("item:tag-too-long")
                raise Reject("tag too long")

        meta = it.get("meta")
        if meta is None:
            hit("item:meta-missing")
            raise Reject("meta missing")
        _check_meta(meta, 1)

    if total > MAX_TOTAL:
        hit("batch:total-too-big")
        raise Reject("total too big")
    hit("batch:valid")
    return True


def _meta_depth(meta):
    return 1 + (_meta_depth(meta["sub"]) if meta.get("sub") else 0)


def process(data):
    """Business logic, reachable only for fully valid batches."""
    hit("process:enter")
    items = data["items"]
    if len(items) >= 5:
        hit("process:large-batch")
    else:
        hit("process:small-batch")
    amounts = [it["amount"] for it in items]
    if max(amounts) == MAX_AMOUNT:
        hit("process:max-amount")
    if sum(amounts) > 1500:
        hit("process:high-value")
    if any(_meta_depth(it["meta"]) >= 3 for it in items):
        hit("process:deep-meta")
    hit("process:done")
    return "OK"


def run(raw):
    data = parse_input(raw)
    validate(data)
    return process(data)
