"""Replay engine with crash-safe checkpointing.

The Replayer feeds the canonical event stream to a projection one event at a
time and persists an *atomic* checkpoint (state + seen ids + processed
position) after every event. If the process is killed mid-replay, a new
Replayer constructed with the same checkpoint file resumes exactly where the
last committed checkpoint left off, producing a result identical to a full
replay from scratch.

`kill_after` simulates a hard kill (e.g. SIGKILL) for testing: after that
many events have been processed in this run, ProjectionKilled is raised
*before* applying the next event, leaving the last checkpoint on disk.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

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
        events = self.store.read_all()
        done_this_run = 0
        for event in events[self.projection.processed_count:]:
            if self.kill_after is not None and done_this_run >= self.kill_after:
                raise ProjectionKilled(
                    f"killed after {done_this_run} event(s) in this run")
            self.projection.apply(event)
            self.projection.processed_count += 1
            done_this_run += 1
            self._save_checkpoint()
        return self.projection

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
