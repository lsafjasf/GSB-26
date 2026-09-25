"""Append-only event store persisted as JSON Lines.

Append is idempotent: re-appending an event whose event_id is already known
is a no-op that returns the original position. This is the first line of
defence against duplicate delivery.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Dict, List

from .events import Event


class EventStore:
    def __init__(self, path: str):
        self.path = path
        self._events: List[Event] = []          # arrival order
        self._positions: Dict[str, int] = {}    # event_id -> position
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    ev = Event.from_dict(json.loads(line))
                    if ev.event_id in self._positions:
                        continue  # tolerate duplicated lines in the log
                    self._positions[ev.event_id] = ev.position
                    self._events.append(ev)

    def __len__(self) -> int:
        return len(self._events)

    def append(self, event: Event) -> int:
        """Append an event; returns its position. Duplicate event_ids are
        ignored (idempotent append)."""
        if event.event_id in self._positions:
            return self._positions[event.event_id]
        positioned = replace(event, position=len(self._events))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(positioned.to_dict(), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._positions[positioned.event_id] = positioned.position
        self._events.append(positioned)
        return positioned.position

    def read_all(self) -> List[Event]:
        """Return the stream in canonical replay order.

        Canonical order = (stream_id, seq, position): events are grouped by
        aggregate and ordered by their producer-assigned logical sequence
        number, so out-of-order *arrival* does not affect replay. The order
        is deterministic for a given log content, which is what makes
        kill/resume equivalent to a full replay.
        """
        return sorted(self._events, key=lambda e: (e.stream_id, e.seq, e.position))
