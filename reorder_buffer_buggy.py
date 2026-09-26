"""Buggy message reorder buffer (pre-fix version, kept for regression repro).

Known defects (each reproduced by test_reorder_buffer.py):
  bug1: a permanently missing sequence number blocks all later messages
        forever (no timeout / gap-skip in push()).
  bug2: duplicate arrivals are delivered twice (no dedup state; "old"
        sequence numbers are re-delivered instead of dropped).
  bug3: sequence-number wraparound breaks dedup/ordering, because plain
        integer comparison (`s < self._next`) is used instead of modular
        arithmetic, and `_next` never wraps.
  bug4: the timeout-release path (flush()) delivers buffered messages but
        does not advance `_next`, so already-delivered messages are
        delivered again once the gap is filled or retransmits arrive.
"""


class BuggyReorderBuffer:
    def __init__(self):
        self._buf = {}
        self._next = None  # next expected sequence number (never wraps: bug3)

    def push(self, seq, payload):
        if self._next is None:
            self._next = seq
        self._buf[seq] = payload
        out = []
        for s in sorted(self._buf):
            if s == self._next:
                # bug1: only exact in-order delivery; no timeout skip.
                out.append((s, self._buf.pop(s)))
                self._next += 1  # bug3: no modular wrap
            elif s < self._next:
                # bug2: stale/duplicate seqs are re-delivered, not dropped.
                # bug3: plain "<" misclassifies wrapped seqs as "old".
                out.append((s, self._buf.pop(s)))
        return out

    def flush(self):
        """Timeout release: dump everything buffered, in sorted order."""
        out = [(s, self._buf[s]) for s in sorted(self._buf)]
        self._buf.clear()
        # bug4: self._next is NOT advanced past the released messages,
        # so they can be delivered a second time later.
        return out
