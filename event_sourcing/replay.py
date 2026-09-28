"""Replay engine with crash-safe, identity-based checkpointing.

The Replayer feeds the canonical event stream to a projection one event at a
time and persists an *atomic* checkpoint after every event. The resume
boundary is the identity of the last processed event -- its (stream_id,
seq) key in canonical order -- never an event count or list index.

Why an identity boundary? While the projection is down, late events can be
appended to the log that sort *before* the boundary in canonical order (e.g.
an event for a stream that was not present before). Those events shift every
canonical index, so slicing `events[processed_count:]` would skip them
forever and the resumed state would diverge from a full replay. On resume we
therefore:

  1. locate the boundary by (stream_id, seq), not by index;
  2. if any *unseen* event sorts at or before the boundary, reset the
     projection and rebuild from the very first event -- this is the only way
     to honour an event that belongs before already-folded state;
  3. otherwise resume from the event immediately after the boundary, still
     skipping any event_id already seen (idempotent duplicate delivery).

The result is item-by-item identical to an uninterrupted full replay.

`kill_after` simulates a hard kill (e.g. SIGKILL) for testing: after that
many events have been processed in this run, ProjectionKilled is raised
*before* applying the next event, leaving the last checkpoint on disk.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional

from .events import Event
from .projections import Projection
from .store import EventStore


class ProjectionKilled(Exception):
    """Simulates a hard kill of the projection process mid-replay."""


class Replayer:
    def __init__(self, store: EventStore, projection: Projection,
                 checkpoint_path: str, kill_after: Optional[int] = None):
        self.store = store
        self.projection = projection
        self.checkpoint_path = checkpoint_path
        self.kill_after = kill_after
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, "r", encoding="utf-8") as fh:
                projection.load_checkpoint(json.load(fh))

    def run(self) -> Projection:
        events: List[Event] = self.store.read_all()
        projection = self.projection

        if projection.boundary_key is not None:
            start_index = self._resume_start(events, projection.boundary_key)
            # Every event strictly before the boundary was folded into the
            # checkpointed state. If an unseen one hides there, a late event
            # was inserted while we were down: reset and rebuild from scratch.
            if any(events[i].event_id not in projection.seen_event_ids
                   for i in range(start_index)):
                projection.reset_to_initial()
                start_index = 0
        else:
            # Fresh projection, or an old count-only checkpoint whose offset
            # cannot be mapped to an identity boundary: start from zero.
            if projection.processed_count:
                projection.reset_to_initial()
            start_index = 0

        done_this_run = 0
        for event in events[start_index:]:
            if self.kill_after is not None and done_this_run >= self.kill_after:
                raise ProjectionKilled(
                    f"killed after {done_this_run} event(s) in this run")
            if event.event_id in projection.seen_event_ids:
                continue
            projection.apply(event)
            projection.mark_applied(event)
            done_this_run += 1
            self._save_checkpoint()
        return projection

    @staticmethod
    def _resume_start(events: List[Event],
                      boundary: tuple) -> int:
        """Index of the first event that sorts strictly *after* the boundary
        key (stream_id, seq). Located by identity, never by stored count."""
        stream_id, seq = boundary
        for index, event in enumerate(events):
            if (event.stream_id, event.seq) > (stream_id, seq):
                return index
        return len(events)

    def _save_checkpoint(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.checkpoint_path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.projection.to_checkpoint(), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.checkpoint_path)  # atomic rename
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
