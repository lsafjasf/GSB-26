from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    """An immutable fact that already happened.

    Dedup / ordering keys:
    - event_id: globally unique identity of a single occurrence; the event
      store rejects duplicate append calls carrying the same event_id.
    - (aggregate_id, seq): 1-based per-aggregate causal sequence number;
      projections use this pair to ignore redelivered events and to reorder
      out-of-order arrivals.
    """

    event_id: str
    aggregate_id: str
    seq: int
    type: str
    version: int
    data: dict

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "aggregate_id": self.aggregate_id,
            "seq": self.seq,
            "type": self.type,
            "version": self.version,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Event":
        return cls(
            event_id=raw["event_id"],
            aggregate_id=raw["aggregate_id"],
            seq=raw["seq"],
            type=raw["type"],
            version=raw["version"],
            data=raw["data"],
        )
