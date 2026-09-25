"""Event definitions.

An Event is an immutable fact. Producers assign:
  - event_id: globally unique id (dedup basis for the whole library)
  - stream_id: the aggregate the event belongs to
  - seq: logical sequence number *within the stream*, assigned by the producer

The store later assigns `position` (arrival order). Because arrival order may
be scrambled (out-of-order delivery), replay never relies on `position` for
ordering; it uses the canonical order (stream_id, seq) instead.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Event:
    event_id: str
    stream_id: str
    seq: int
    type: str
    version: int
    data: Dict[str, Any]
    position: int = -1  # arrival index, assigned by EventStore.append

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Event":
        return cls(**raw)
