from __future__ import annotations

from .events import Event


class EventStore:
    """Append-only store.

    Appends receive a monotonically increasing 1-based global position.
    Append is idempotent on ``event_id``: appending the same event again
    returns the original position instead of adding a second copy. This makes
    producer retries / at-least-once delivery safe.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._positions: dict[str, int] = {}

    def append(self, event: Event) -> int:
        existing = self._positions.get(event.event_id)
        if existing is not None:
            return existing
        position = len(self._events) + 1
        self._events.append(event)
        self._positions[event.event_id] = position
        return position

    def append_all(self, events) -> list[int]:
        return [self.append(event) for event in events]

    def read_from(self, position: int = 0):
        for index in range(position, len(self._events)):
            yield index + 1, self._events[index]

    def position_of(self, event_id: str) -> int | None:
        return self._positions.get(event_id)

    def __len__(self) -> int:
        return len(self._events)
