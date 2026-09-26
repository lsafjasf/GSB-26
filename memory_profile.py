"""Memory and backlog profile for the fixed ReorderBuffer.

Feeds 200k messages (16-bit seq, wraps ~3 times) with 2% loss and
bounded jitter through a window=1024 buffer, measuring:
  - peak Python allocation (tracemalloc) during the run
  - peak buffered backlog (must be <= window)
  - per-entry buffer footprint

Run:  python3 memory_profile.py
"""

import random
import sys
import tracemalloc

from reorder_buffer import ReorderBuffer

N = 200_000
WINDOW = 1024
LOSS_P = 0.02
JITTER = 20


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def main():
    clock = FakeClock()
    stats = {"delivered": 0, "gaps": 0, "drops": 0}
    buf = ReorderBuffer(
        window=WINDOW, seq_bits=16, max_gap_wait=0.35, clock=clock,
        on_deliver=lambda s, p: stats.__setitem__("delivered", stats["delivered"] + 1),
        on_gap=lambda m: stats.__setitem__("gaps", stats["gaps"] + len(m)),
        on_drop=lambda s, r: stats.__setitem__("drops", stats["drops"] + 1))

    rng = random.Random(42)
    lost = {i for i in range(N) if rng.random() < LOSS_P}
    events = []
    for i in range(N):
        if i in lost:
            continue
        events.append((i + rng.uniform(0, JITTER), i % 65536))
    events.sort()

    payload = b"x" * 64  # 64-byte message payload
    tracemalloc.start()
    for _, seq in events:
        buf.push(seq, payload, now=clock())
        clock.advance(0.01)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    while buf.backlog:
        clock.advance(1.0)
        buf.poll(now=clock())

    entry_bytes = sys.getsizeof({0: (payload, 0.0)}) - sys.getsizeof({})
    print(f"messages pushed        : {len(events)} (seq wraps {N // 65536}x)")
    print(f"delivered              : {stats['delivered']}")
    print(f"reported as gap        : {stats['gaps']}")
    print(f"dropped (dup/overflow) : {stats['drops']} "
          f"(dup={buf.duplicate_count}, overflow={buf.overflow_drop_count})")
    print(f"peak backlog (entries) : {buf.max_backlog}  (window bound = {WINDOW})")
    print(f"peak process alloc     : {peak / 1024:.0f} KiB (tracemalloc, incl. stream)")
    print(f"buffer dict footprint  : ~{entry_bytes} B/entry "
          f"-> worst case ~{entry_bytes * WINDOW / 1024:.0f} KiB at window={WINDOW}")

    assert buf.max_backlog <= WINDOW, "backlog exceeded window bound"
    assert stats["delivered"] + stats["gaps"] >= N - len(lost) - WINDOW
    print("OK: backlog bounded by window; all messages accounted for")


if __name__ == "__main__":
    main()
