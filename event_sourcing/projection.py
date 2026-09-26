from __future__ import annotations

import abc
import json
import os

from .events import Event


class IncompatibleVersionError(Exception):
    """Raised when an event type/version is not supported by a projection."""


class SimulatedKill(RuntimeError):
    """Raised by tests to emulate the process being killed mid-rebuild.

    It is raised *after* an event was applied to memory but *before* its
    checkpoint is committed, modelling the worst case (work lost on kill).
    """


class SequencedProjection(abc.ABC):
    """Base projection with idempotent, order-tolerant replay.

    Checkpoint state is plain JSON data:
        {"next_seq": {aggregate_id: next_expected_seq},
         "pending":  {aggregate_id: {str(seq): event_dict}},
         "data":     <projection specific state>}

    Dedup basis: (aggregate_id, seq). Events for an aggregate are applied in
    strict seq order. An event whose seq is below the next expected seq was
    already applied (duplicate delivery) and is skipped. Future-seq events
    are buffered until the gap closes, so arrival order does not affect the
    resulting state. The full state (including the pending buffer) is
    checkpointed, so resume after a kill produces exactly the same state as
    a from-scratch replay.
    """

    name: str = "projection"
    supported_versions: dict[str, frozenset] = {}

    @abc.abstractmethod
    def initial_data(self) -> dict:
        """Projection-specific initial state."""

    def initial_state(self) -> dict:
        return {"next_seq": {}, "pending": {}, "data": self.initial_data()}

    def apply(self, state: dict, event: Event) -> None:
        supported = self.supported_versions.get(event.type)
        if supported is None or event.version not in supported:
            raise IncompatibleVersionError(
                f"{self.name}: event {event.type} v{event.version} "
                f"(event_id={event.event_id}) is not supported"
            )

        aggregate_id = event.aggregate_id
        next_seq = state["next_seq"].get(aggregate_id, 1)
        if event.seq < next_seq:
            return

        pending = state["pending"].setdefault(aggregate_id, {})
        pending.setdefault(str(event.seq), event.to_dict())
        self._drain(state, aggregate_id)

    def _drain(self, state: dict, aggregate_id: str) -> None:
        pending = state["pending"][aggregate_id]
        next_seq = state["next_seq"].get(aggregate_id, 1)
        while str(next_seq) in pending:
            event = Event.from_dict(pending.pop(str(next_seq)))
            self.apply_event(state["data"], event)
            next_seq += 1
        state["next_seq"][aggregate_id] = next_seq
        if not pending:
            del state["pending"][aggregate_id]

    @abc.abstractmethod
    def apply_event(self, data: dict, event: Event) -> None:
        """Apply a single event known to be the next seq of its aggregate."""


class CheckpointStore:
    """Atomic JSON checkpoint: {position, state}."""

    def __init__(self, directory: str, projection_name: str) -> None:
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, f"{projection_name}.checkpoint.json")

    def load(self) -> tuple[int, dict]:
        if not os.path.exists(self.path):
            return 0, {}
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload["position"], payload["state"]

    def save(self, position: int, state: dict) -> None:
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump({"position": position, "state": state}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self.path)

    def reset(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)


class ProjectionRunner:
    def __init__(
        self,
        store,
        projection: SequencedProjection,
        checkpoint_dir: str,
        commit_interval: int = 1,
    ) -> None:
        self.store = store
        self.projection = projection
        self.commit_interval = commit_interval
        self.checkpoints = CheckpointStore(checkpoint_dir, projection.name)

    def rebuild(self, kill_after: int | None = None) -> dict:
        self.checkpoints.reset()
        return self.resume(kill_after=kill_after)

    def resume(self, kill_after: int | None = None) -> dict:
        position, state = self.checkpoints.load()
        if not state:
            state = self.projection.initial_state()

        processed = 0
        last_position = position
        for last_position, event in self.store.read_from(position):
            self.projection.apply(state, event)
            processed += 1
            if kill_after is not None and processed >= kill_after:
                raise SimulatedKill(
                    f"killed after processing event at position {last_position}"
                )
            if processed % self.commit_interval == 0:
                self.checkpoints.save(last_position, state)

        self.checkpoints.save(last_position, state)
        return state

    def checkpoint_position(self) -> int:
        position, _ = self.checkpoints.load()
        return position
