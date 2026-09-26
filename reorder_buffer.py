"""Fixed message reorder buffer (Python 3, stdlib only).

Reorders out-of-order messages by sequence number and delivers them
upstream strictly in order, without duplicates, across sequence-number
wraparound, with a hard bound on buffered backlog.

Design notes
------------
Sequence numbers are taken modulo 2**seq_bits (default 16, like RTP).
All comparisons use signed modular distance:

    diff(a, b) = (a - b) mod 2**seq_bits, mapped into [-2**(bits-1), 2**(bits-1))

so ordering and dedup are wrap-safe. The window must be < 2**(bits-1)
so that "ahead" vs "behind" is never ambiguous.

Dedup rule (verifiable):
    Let next_expected be the next sequence number to deliver.
    A pushed seq is a duplicate/too-old iff
        diff(seq, next_expected) < 0        (already delivered or skipped)
        or seq is already buffered          (retransmit of a pending msg)
    A pushed seq is overflow iff diff(seq, next_expected) >= window.

Gap handling:
    While a gap at next_expected persists, later messages are buffered.
    The gap is skipped (reported via on_gap, then delivery continues)
    when either:
      * the gap has waited >= max_gap_wait seconds (wall clock), or
      * the gap width exceeds max_gap sequence numbers (if configured).
    Missing seqs are reported explicitly; messages arriving later for
    skipped seqs are dropped as "late" (counted, reported via on_drop).
    Set max_gap_wait=None to disable time-based skipping (not
    recommended: reintroduces the indefinite-blocking failure mode).

Anchoring:
    The first pushed sequence number anchors the receive window. Any
    subsequently arriving seq that lies behind it (modularly) is treated
    as already-delivered and dropped. This is standard jitter-buffer
    behaviour; callers that need an earlier start must push it first.

Bounded memory:
    At most `window` messages are ever buffered; anything farther ahead
    is dropped as overflow and reported. Buffer occupancy is therefore
    O(window) regardless of input volume.
"""

import time


def _modular_diff_fn(seq_bits):
    mod = 1 << seq_bits
    half = mod >> 1

    def diff(a, b):
        """Signed modular distance a - b, in [-half, half)."""
        d = (a - b) % mod
        if d >= half:
            d -= mod
        return d

    return diff, mod, half


class ReorderBuffer:
    def __init__(self, *, window=1024, max_gap_wait=2.0, max_gap=None,
                 seq_bits=16, clock=time.monotonic,
                 on_deliver=None, on_gap=None, on_drop=None):
        self._diff, self.mod, self.half = _modular_diff_fn(seq_bits)
        if not 1 <= window < self.half:
            raise ValueError("window must be in [1, 2**(seq_bits-1))")
        self.window = window
        self.max_gap_wait = max_gap_wait
        self.max_gap = max_gap
        self._clock = clock
        self._on_deliver = on_deliver or (lambda seq, payload: None)
        self._on_gap = on_gap or (lambda missing: None)
        self._on_drop = on_drop or (lambda seq, reason: None)

        self._buf = {}            # seq -> (payload, arrival_time)
        self._next = None         # next expected seq (mod 2**seq_bits)
        self._last_delivered = None
        self._gap_since = None    # when the current gap was first observed

        # observable stats
        self.delivered_count = 0
        self.duplicate_count = 0
        self.overflow_drop_count = 0
        self.gap_count = 0
        self.max_backlog = 0      # peak buffered messages

    @property
    def backlog(self):
        return len(self._buf)

    def push(self, seq, payload, now=None):
        now = self._clock() if now is None else now
        if not 0 <= seq < self.mod:
            raise ValueError("seq out of range for seq_bits")
        if self._next is None:
            self._next = seq

        d = self._diff(seq, self._next)
        if d < 0:
            # Behind the receive window: already delivered or already
            # skipped as part of a reported gap. Drop, never re-deliver.
            self.duplicate_count += 1
            self._on_drop(seq, "duplicate")
            return
        if d >= self.window:
            # Too far ahead: would exceed the memory bound. Drop.
            self.overflow_drop_count += 1
            self._on_drop(seq, "overflow")
            return
        if seq in self._buf:
            self.duplicate_count += 1
            self._on_drop(seq, "duplicate")
            return

        self._buf[seq] = (payload, now)
        if len(self._buf) > self.max_backlog:
            self.max_backlog = len(self._buf)
        if d > 0 and self._gap_since is None:
            self._gap_since = now
        self._drain(now)

    def poll(self, now=None):
        """Enforce gap timeouts even when no new messages arrive."""
        self._drain(self._clock() if now is None else now)

    def _emit(self, seq, payload):
        self._last_delivered = seq
        self._next = (seq + 1) % self.mod
        self.delivered_count += 1
        self._on_deliver(seq, payload)

    def _drain(self, now):
        if self._next is None:
            return
        while True:
            while self._next in self._buf:
                payload, _ = self._buf.pop(self._next)
                self._emit(self._next, payload)
            if not self._buf:
                self._gap_since = None
                return
            # All buffered seqs are strictly ahead of _next (see push).
            gap_size = min(self._diff(s, self._next) for s in self._buf)
            if self._gap_since is None:
                self._gap_since = now
            timed_out = (self.max_gap_wait is not None
                         and now - self._gap_since >= self.max_gap_wait)
            too_wide = (self.max_gap is not None
                        and gap_size > self.max_gap)
            if not (timed_out or too_wide):
                return
            # Skip the gap: report the missing seqs, advance, continue.
            missing = [(self._next + i) % self.mod for i in range(gap_size)]
            self._next = (self._next + gap_size) % self.mod
            self._gap_since = None
            self.gap_count += 1
            self._on_gap(missing)
