"""Vulnerable implementation retained to make regression failures reproducible.

Do not import this module from production code.
"""

from __future__ import annotations

import os
import threading
import time


_counter = 0


def _current_second() -> int:
    frozen = os.environ.get("LEGACY_FROZEN_SECOND")
    if frozen is not None:
        return int(frozen)
    return int(time.time())


def _race_barrier(workers: int = 16) -> None:
    if os.environ.get("LEGACY_ENABLE_RACE_BARRIER") != "1":
        return
    barrier = getattr(_race_barrier, "_barrier", None)
    if barrier is None or barrier.parties != workers:
        barrier = threading.Barrier(workers)
        _race_barrier._barrier = barrier
    barrier.wait()


def reset_counter() -> None:
    global _counter
    _counter = 0


def generate_session_id(length: int = 16) -> str:
    global _counter
    observed = _counter
    _race_barrier()
    _counter = observed + 1
    second = _current_second()
    return f"{second:010}{observed:06d}"[:length]


def generate_salt(length: int = 8) -> str:
    return generate_session_id(length)
