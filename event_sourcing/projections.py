"""Projections: derive in-memory state from the event stream.

Idempotency / dedup basis
-------------------------
Every projection keeps `seen_event_ids`. `apply()` skips any event whose
event_id was already processed, so delivering the same event twice never
changes the resulting state. The dedup key is the producer-assigned,
globally unique `event_id`.

Checkpointing
-------------
A projection can snapshot {processed_count, seen_event_ids, state} to JSON
and restore from it. `processed_count` is the offset into the canonical
stream (see EventStore.read_all), i.e. "the last processed event position".
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .events import Event


class ReplayError(Exception):
    """Base class for replay failures."""


class UnknownEventTypeError(ReplayError):
    """The projection has no handler for this event type."""


class IncompatibleEventVersionError(ReplayError):
    """Event version is not supported and no upcaster chain exists."""


# (event_type, from_version) -> fn(data of vN) -> data of vN+1
_UPCASTERS: Dict[Tuple[str, int], Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def register_upcaster(event_type: str, from_version: int,
                      fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    _UPCASTERS[(event_type, from_version)] = fn


class Projection:
    name: str = "projection"
    supported_versions: Dict[str, Set[int]] = {}
    handlers: Dict[str, Callable[[Dict[str, Any], Dict[str, Any], Event], None]] = {}

    def __init__(self) -> None:
        self.state: Dict[str, Any] = self.initial_state()
        self.seen_event_ids: Set[str] = set()
        self.processed_count: int = 0  # number of distinct events applied
        # Identity-based resume boundary: the (stream_id, seq) of the last
        # processed event. None until the first event is applied.
        self.last_stream_id: Optional[str] = None
        self.last_seq: Optional[int] = None

    def initial_state(self) -> Dict[str, Any]:
        return {}

    def _upcast(self, event: Event) -> Dict[str, Any]:
        supported = self.supported_versions.get(event.type)
        if supported is None:
            raise UnknownEventTypeError(
                f"{self.name}: no handler for event type {event.type!r}")
        version = event.version
        data = event.data
        while version not in supported:
            fn = _UPCASTERS.get((event.type, version))
            if fn is None:
                raise IncompatibleEventVersionError(
                    f"{self.name}: {event.type} v{version} is not supported "
                    f"(supported: {sorted(supported)}) and no upcaster is registered")
            data = fn(data)
            version += 1
        return data

    def apply(self, event: Event) -> None:
        """Apply one event. Idempotent: duplicate event_ids are skipped."""
        if event.event_id in self.seen_event_ids:
            return
        data = self._upcast(event)
        self.handlers[event.type](self.state, data, event)
        self.seen_event_ids.add(event.event_id)

    def reset_to_initial(self) -> None:
        """Forget all derived state (used when a late event inserted before
        the checkpoint boundary forces a full rebuild)."""
        self.state = self.initial_state()
        self.seen_event_ids = set()
        self.processed_count = 0
        self.last_stream_id = None
        self.last_seq = None

    def mark_applied(self, event: Event) -> None:
        """Advance the identity-based checkpoint boundary past `event`.
        Called by the Replayer after every distinct event is applied."""
        self.processed_count += 1
        self.last_stream_id = event.stream_id
        self.last_seq = event.seq

    @property
    def boundary_key(self) -> Optional[Tuple[str, int]]:
        """Canonical-order key of the last processed event."""
        if self.last_stream_id is None:
            return None
        return self.last_stream_id, self.last_seq

    # -- checkpointing -----------------------------------------------------
    def to_checkpoint(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "processed_count": self.processed_count,
            "last_stream_id": self.last_stream_id,
            "last_seq": self.last_seq,
            "seen_event_ids": sorted(self.seen_event_ids),
            "state": self.state,
        }

    def load_checkpoint(self, snap: Dict[str, Any]) -> None:
        if snap["name"] != self.name:
            raise ReplayError(
                f"checkpoint belongs to {snap['name']!r}, not {self.name!r}")
        self.processed_count = snap["processed_count"]
        self.seen_event_ids = set(snap["seen_event_ids"])
        self.state = snap["state"]
        # Identity-based boundary. Checkpoints written by older versions only
        # stored processed_count; they cannot be resumed by offset, so the
        # Replayer rebuilds them from scratch (handled there).
        self.last_stream_id = snap.get("last_stream_id")
        self.last_seq = snap.get("last_seq")


# --------------------------------------------------------------------------
# Domain: simple bank accounts.
# Events: AccountOpened(v1), Deposited(v2, v1 via upcaster),
#         Withdrawn(v2, v1 via upcaster).
# v1 payloads used {"amount": <dollars float>}; v2 uses {"amount_cents": int}.
# --------------------------------------------------------------------------

def _upcast_money_v1(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"amount_cents": round(data["amount"] * 100)}


register_upcaster("Deposited", 1, _upcast_money_v1)
register_upcaster("Withdrawn", 1, _upcast_money_v1)


def _opened_detail(state, data, event):
    state["accounts"][event.stream_id] = {"owner": data["owner"], "txs": []}


def _deposited_detail(state, data, event):
    state["accounts"][event.stream_id]["txs"].append({
        "event_id": event.event_id, "kind": "deposit",
        "amount_cents": data["amount_cents"], "seq": event.seq,
    })


def _withdrawn_detail(state, data, event):
    state["accounts"][event.stream_id]["txs"].append({
        "event_id": event.event_id, "kind": "withdrawal",
        "amount_cents": data["amount_cents"], "seq": event.seq,
    })


class DetailViewProjection(Projection):
    """Per-account transaction ledger (detail view)."""
    name = "detail-view"
    supported_versions = {"AccountOpened": {1}, "Deposited": {2}, "Withdrawn": {2}}
    handlers = {
        "AccountOpened": _opened_detail,
        "Deposited": _deposited_detail,
        "Withdrawn": _withdrawn_detail,
    }

    def initial_state(self):
        return {"accounts": {}}


def _opened_agg(state, data, event):
    state["accounts"][event.stream_id] = {
        "balance_cents": 0, "deposit_count": 0,
        "withdrawal_count": 0, "tx_count": 0,
    }


def _deposited_agg(state, data, event):
    acc = state["accounts"][event.stream_id]
    acc["balance_cents"] += data["amount_cents"]
    acc["deposit_count"] += 1
    acc["tx_count"] += 1


def _withdrawn_agg(state, data, event):
    acc = state["accounts"][event.stream_id]
    acc["balance_cents"] -= data["amount_cents"]
    acc["withdrawal_count"] += 1
    acc["tx_count"] += 1


class AggregateViewProjection(Projection):
    """Per-account balance and counters (aggregate view)."""
    name = "aggregate-view"
    supported_versions = {"AccountOpened": {1}, "Deposited": {2}, "Withdrawn": {2}}
    handlers = {
        "AccountOpened": _opened_agg,
        "Deposited": _deposited_agg,
        "Withdrawn": _withdrawn_agg,
    }

    def initial_state(self):
        return {"accounts": {}}


def view_inconsistencies(detail_state: Dict[str, Any],
                         aggregate_state: Dict[str, Any]) -> List[str]:
    """Cross-check the two projections: the aggregate view must be exactly
    derivable from the detail view. Returns a list of problems (empty == OK)."""
    problems: List[str] = []
    d_accs = detail_state["accounts"]
    a_accs = aggregate_state["accounts"]
    if set(d_accs) != set(a_accs):
        problems.append(
            f"account sets differ: detail={sorted(d_accs)} aggregate={sorted(a_accs)}")
        return problems
    for sid in sorted(d_accs):
        txs = d_accs[sid]["txs"]
        signed = sum(t["amount_cents"] if t["kind"] == "deposit"
                     else -t["amount_cents"] for t in txs)
        agg = a_accs[sid]
        if signed != agg["balance_cents"]:
            problems.append(
                f"{sid}: detail sum {signed} != aggregate balance {agg['balance_cents']}")
        if len(txs) != agg["tx_count"]:
            problems.append(
                f"{sid}: detail tx count {len(txs)} != aggregate {agg['tx_count']}")
        dep = sum(1 for t in txs if t["kind"] == "deposit")
        if dep != agg["deposit_count"]:
            problems.append(
                f"{sid}: detail deposits {dep} != aggregate {agg['deposit_count']}")
    return problems
