from __future__ import annotations

from .events import Event
from .projection import SequencedProjection

SUPPORTED_VERSIONS = {
    "AccountOpened": frozenset({1}),
    "MoneyDeposited": frozenset({1}),
    "MoneyWithdrawn": frozenset({1, 2}),
}


def signed_amount(event: Event) -> int:
    """Balance delta for an event, in cents. Shared by both projections."""
    if event.type == "AccountOpened":
        return 0
    if event.type == "MoneyDeposited":
        return event.data["amount"]
    if event.type == "MoneyWithdrawn":
        total = event.data["amount"]
        if event.version >= 2:
            total += event.data["fee"]
        return -total
    raise ValueError(f"unknown event type: {event.type}")


class AccountDetailProjection(SequencedProjection):
    """Fine-grained view: ordered ledger with running balance per account."""

    name = "account_detail"
    supported_versions = SUPPORTED_VERSIONS

    def initial_data(self) -> dict:
        return {"accounts": {}}

    def apply_event(self, data: dict, event: Event) -> None:
        account = data["accounts"].setdefault(
            event.aggregate_id, {"entries": []}
        )
        balance = account["entries"][-1]["balance"] if account["entries"] else 0
        amount = signed_amount(event)
        account["entries"].append(
            {
                "event_id": event.event_id,
                "seq": event.seq,
                "type": event.type,
                "version": event.version,
                "amount": amount,
                "balance": balance + amount,
            }
        )


class AccountSummaryProjection(SequencedProjection):
    """Aggregate view: balance and counters per account."""

    name = "account_summary"
    supported_versions = SUPPORTED_VERSIONS

    def initial_data(self) -> dict:
        return {"accounts": {}}

    def apply_event(self, data: dict, event: Event) -> None:
        account = data["accounts"].setdefault(
            event.aggregate_id,
            {"balance": 0, "deposits": 0, "withdrawals": 0, "tx_count": 0},
        )
        amount = signed_amount(event)
        account["balance"] += amount
        account["tx_count"] += 1
        if amount > 0:
            account["deposits"] += amount
        elif amount < 0:
            account["withdrawals"] += -amount
