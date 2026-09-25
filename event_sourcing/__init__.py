"""A minimal event-sourcing library (standard library only).

Public API:
  - Event, new_event_id          : immutable events with unique ids
  - EventStore                   : idempotent append + canonical-order read
  - Projection + two built-ins   : DetailViewProjection, AggregateViewProjection
  - Replayer                     : checkpointed replay, kill/resume safe
  - view_inconsistencies         : cross-projection consistency check
"""
from .events import Event, new_event_id
from .projections import (
    AggregateViewProjection,
    DetailViewProjection,
    IncompatibleEventVersionError,
    Projection,
    ReplayError,
    UnknownEventTypeError,
    register_upcaster,
    view_inconsistencies,
)
from .replay import ProjectionKilled, Replayer
from .store import EventStore

__all__ = [
    "AggregateViewProjection",
    "DetailViewProjection",
    "Event",
    "EventStore",
    "IncompatibleEventVersionError",
    "Projection",
    "ProjectionKilled",
    "ReplayError",
    "Replayer",
    "UnknownEventTypeError",
    "new_event_id",
    "register_upcaster",
    "view_inconsistencies",
]
