"""Regression tests: reproduce the 4 production bugs on the buggy
implementation, then verify the fixed implementation.

Run:  python3 test_reorder_buffer.py -v
"""

import random
import unittest

from reorder_buffer import ReorderBuffer
from reorder_buffer_buggy import BuggyReorderBuffer


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def mod_diff(a, b, bits):
    mod = 1 << bits
    half = mod >> 1
    d = (a - b) % mod
    return d - mod if d >= half else d


def assert_strictly_increasing_mod(testcase, seqs, bits):
    """Delivered sequence must strictly increase in modular order."""
    testcase.assertEqual(len(seqs), len(set(seqs)), "duplicate delivery")
    for a, b in zip(seqs, seqs[1:]):
        d = mod_diff(b, a, bits)
        testcase.assertGreater(d, 0, f"non-increasing delivery: {a} -> {b}")


class Harness:
    """Collects callbacks from a fixed ReorderBuffer."""

    def __init__(self, **kw):
        self.clock = FakeClock()
        self.delivered = []
        self.gaps = []
        self.drops = []
        kw.setdefault("clock", self.clock)
        self.buf = ReorderBuffer(
            on_deliver=lambda s, p: self.delivered.append((s, p)),
            on_gap=lambda missing: self.gaps.append(list(missing)),
            on_drop=lambda s, r: self.drops.append((s, r)),
            **kw)

    def push(self, seq, payload=None):
        self.buf.push(seq, payload if payload is not None else f"m{seq}",
                      now=self.clock())

    def seqs(self):
        return [s for s, _ in self.delivered]


# ---------------------------------------------------------------------------
# Part 1: reproduce the four production bugs against the buggy version.
# These tests PASS by asserting the defective behaviour occurs.
# ---------------------------------------------------------------------------

class BuggyReproTest(unittest.TestCase):

    def test_bug1_gap_blocks_forever(self):
        b = BuggyReorderBuffer()
        out = []
        for seq in (0, 1, 3, 4, 5, 6):  # seq 2 never arrives
            out += b.push(seq, f"m{seq}")
        self.assertEqual([s for s, _ in out], [0, 1])
        # ...and it stays blocked no matter how many more arrive:
        for seq in (7, 8, 9):
            out += b.push(seq, f"m{seq}")
        self.assertEqual([s for s, _ in out], [0, 1],
                         "bug1 reproduced: gap at 2 blocks 3..9 indefinitely")

    def test_bug2_duplicate_delivered_twice(self):
        b = BuggyReorderBuffer()
        out = b.push(0, "m0")
        out += b.push(0, "m0")  # retransmit
        self.assertEqual([s for s, _ in out], [0, 0],
                         "bug2 reproduced: duplicate delivered twice")

    def test_bug3_wraparound_breaks_dedup(self):
        b = BuggyReorderBuffer()
        out = []
        for seq in (65533, 65534, 65535):
            out += b.push(seq, f"m{seq}")
        # After wrap, seq 0 is the next message. Buggy code compares with
        # plain "<", so 0 < 65536 and it is misclassified as "old" and
        # dumped out of order; a retransmit of 65535 is also re-delivered.
        out += b.push(0, "m0")
        out += b.push(65535, "m65535")  # retransmit
        seqs = [s for s, _ in out]
        self.assertEqual(seqs, [65533, 65534, 65535, 0, 65535],
                         "bug3 reproduced: wrap breaks ordering and dedup")

    def test_bug4_flush_redelivers(self):
        b = BuggyReorderBuffer()
        out = b.push(0, "m0") + b.push(1, "m1")
        out += b.push(3, "m3") + b.push(4, "m4")   # gap at 2: blocked
        self.assertEqual([s for s, _ in out], [0, 1])
        out += b.flush()                            # timeout release
        self.assertEqual([s for s, _ in out], [0, 1, 3, 4])
        out += b.push(2, "m2")                      # gap finally filled
        out += b.push(3, "m3")                      # retransmit of 3
        self.assertEqual([s for s, _ in out], [0, 1, 3, 4, 2, 3],
                         "bug4 reproduced: 3 delivered again after flush")


# ---------------------------------------------------------------------------
# Part 2: the fixed implementation.
# ---------------------------------------------------------------------------

class FixedBasicTest(unittest.TestCase):

    def test_out_of_order_delivery(self):
        h = Harness(window=16)
        for seq in (0, 2, 4, 3, 1):   # first push anchors the window
            h.push(seq)
        self.assertEqual(h.seqs(), [0, 1, 2, 3, 4])
        self.assertEqual(h.buf.gap_count, 0)

    def test_anchor_semantics(self):
        # The first pushed seq anchors the window; modularly-earlier
        # arrivals are treated as old and dropped (documented behaviour).
        h = Harness(window=16)
        h.push(5)
        h.push(3)
        self.assertEqual(h.seqs(), [5])
        self.assertIn((3, "duplicate"), h.drops)

    def test_duplicate_not_delivered_twice(self):
        h = Harness(window=16)
        for seq in (0, 1, 1, 2, 1, 0, 2):
            h.push(seq)
        self.assertEqual(h.seqs(), [0, 1, 2])
        self.assertEqual(h.buf.duplicate_count, 4)
        self.assertTrue(all(r == "duplicate" for _, r in h.drops))

    def test_gap_timeout_reports_and_continues(self):
        h = Harness(window=16, max_gap_wait=2.0)
        h.push(0)
        h.push(1)
        h.push(3)                      # gap at 2 starts now (t=0)
        self.assertEqual(h.seqs(), [0, 1])
        h.clock.advance(1.0)
        h.push(4)                      # 1s < 2s: still waiting
        self.assertEqual(h.seqs(), [0, 1])
        h.clock.advance(1.5)           # t=2.5 >= 2.0: gap times out
        h.push(5)
        self.assertEqual(h.seqs(), [0, 1, 3, 4, 5])
        self.assertEqual(h.gaps, [[2]], "gap must be reported explicitly")
        # A late arrival for the skipped seq is dropped, not delivered.
        h.push(2)
        self.assertEqual(h.seqs(), [0, 1, 3, 4, 5])
        self.assertIn((2, "duplicate"), h.drops)

    def test_gap_timeout_via_poll_without_new_messages(self):
        h = Harness(window=16, max_gap_wait=1.0)
        h.push(0)
        h.push(2)
        self.assertEqual(h.seqs(), [0])
        h.clock.advance(1.1)
        h.buf.poll(now=h.clock())
        self.assertEqual(h.seqs(), [0, 2])
        self.assertEqual(h.gaps, [[1]])

    def test_no_deadlock_repeated_gaps(self):
        """Every gap eventually resolves; delivery always catches up."""
        h = Harness(window=64, max_gap_wait=0.5)
        sent = list(range(200))
        missing = {10, 55, 56, 120}
        for seq in sent:
            if seq in missing:
                continue
            h.push(seq)
            h.clock.advance(0.6)  # force a timeout after each gap
        expect = [s for s in sent if s not in missing]
        self.assertEqual(h.seqs(), expect)
        self.assertEqual(sorted(m for g in h.gaps for m in g),
                         sorted(missing))

    def test_max_gap_count_skip(self):
        h = Harness(window=64, max_gap_wait=None, max_gap=4)
        h.push(0)
        for seq in range(6, 10):   # gap of 5 > max_gap=4 -> skip by count
            h.push(seq)
        self.assertEqual(h.seqs(), [0, 6, 7, 8, 9])
        self.assertEqual(h.gaps, [[1, 2, 3, 4, 5]])


class FixedWraparoundTest(unittest.TestCase):

    def test_wraparound_ordering(self):
        h = Harness(window=16, seq_bits=16)
        for seq in (65534, 65535, 0, 1, 2):
            h.push(seq)
        self.assertEqual(h.seqs(), [65534, 65535, 0, 1, 2])
        assert_strictly_increasing_mod(self, h.seqs(), 16)

    def test_wraparound_out_of_order(self):
        h = Harness(window=16, seq_bits=16)
        for seq in (65534, 0, 65535, 1):   # shuffled across the wrap point
            h.push(seq)
        self.assertEqual(h.seqs(), [65534, 65535, 0, 1])

    def test_wraparound_dedup(self):
        h = Harness(window=16, seq_bits=16)
        for seq in (65534, 65535, 0, 1):
            h.push(seq)
        for seq in (65534, 65535, 0):      # retransmits across the wrap
            h.push(seq)
        self.assertEqual(h.seqs(), [65534, 65535, 0, 1])
        self.assertEqual(h.buf.duplicate_count, 3)

    def test_dedup_boundary(self):
        """Verifiable dedup rule at exact boundaries (small modulus)."""
        # seq_bits=4 -> mod=16, half=8, window=4. Deliver up to seq 2.
        h = Harness(window=4, seq_bits=4, max_gap_wait=100)
        for seq in (0, 1, 2):
            h.push(seq)
        # dedup rule: diff(seq, next_expected=3) < 0  ->  duplicate
        h.push(2)    # diff -1 -> duplicate
        h.push(0)    # diff -3 -> duplicate
        h.push(11)   # diff (11-3)%16 = 8 -> mapped to -8: the ambiguous
                     # half-space point, defined as "old" -> duplicate
        self.assertEqual(h.buf.duplicate_count, 3)
        self.assertEqual(h.seqs(), [0, 1, 2])
        # diff == 0 -> new, delivered
        h.push(3)
        self.assertEqual(h.seqs(), [0, 1, 2, 3])
        # 0 < diff < window -> buffered
        h.push(6)    # diff(6,4)=2, gap at 4,5
        self.assertEqual(h.buf.backlog, 1)
        # diff >= window -> overflow drop
        h.push(8)    # diff(8,4)=4 >= window=4
        self.assertIn((8, "overflow"), h.drops)
        self.assertEqual(h.buf.backlog, 1)
        # after the gap times out, late arrivals for skipped seqs drop
        h.clock.advance(200)
        h.buf.poll(now=h.clock())
        self.assertEqual(h.seqs(), [0, 1, 2, 3, 6])
        self.assertEqual(h.gaps, [[4, 5]])
        h.push(5)    # diff(5,7) = -2 -> late, dropped
        self.assertIn((5, "duplicate"), h.drops)
        self.assertEqual(h.seqs(), [0, 1, 2, 3, 6])

    def test_full_cycle_no_duplicates(self):
        """Feed two full wrap cycles; delivery stays strictly increasing."""
        h = Harness(window=256, seq_bits=16, max_gap_wait=0.5)
        n = 2 * 65536 + 100
        for i in range(n):
            h.push(i % 65536)
            if i % 997 == 0:
                h.clock.advance(0.01)
        self.assertEqual(h.buf.delivered_count, n)
        seqs = h.seqs()
        for a, b in zip(seqs, seqs[1:]):
            self.assertGreater(mod_diff(b, a, 16), 0,
                               f"non-increasing delivery: {a} -> {b}")


class FixedBoundAndPropertyTest(unittest.TestCase):

    def test_buffer_bound_and_overflow_reporting(self):
        h = Harness(window=8, max_gap_wait=1000)
        h.push(0)
        for seq in range(2, 8):      # gap at 1; 2..7 buffered (6 msgs)
            h.push(seq)
        self.assertEqual(h.buf.backlog, 6)
        h.push(9)                    # diff(9,1)=8 >= window -> overflow
        self.assertIn((9, "overflow"), h.drops)
        self.assertLessEqual(h.buf.backlog, 8)
        self.assertLessEqual(h.buf.max_backlog, 8)

    def test_randomized_property(self):
        """Random loss/dup/reorder: output strictly increasing, no dup,
        and every input is accounted for (delivered / gapped / dropped)."""
        rng = random.Random(20260926)
        bits, window = 16, 512
        # Feasibility requires: max jitter < gap timeout (in messages)
        # < mean distance between losses. Here: 20 < 35 < 50.
        h = Harness(window=window, seq_bits=bits, max_gap_wait=0.35)
        n = 20000  # < 2**16, so no wrap in this scenario
        sent = list(range(n))
        lost = {s for s in sent if rng.random() < 0.02}
        events = []
        for s in sent:
            if s in lost:
                continue
            t = s + rng.uniform(0, 20)       # bounded jitter
            events.append((t, s))
            if rng.random() < 0.1:
                events.append((t + rng.uniform(0, 10), s))   # retransmit
        events.sort(key=lambda e: e[0])
        stream = [s for _, s in events]
        if 0 not in lost:
            stream.remove(0)
            stream.insert(0, 0)   # anchor the window at seq 0
        for s in stream:
            h.push(s)
            h.clock.advance(0.01)  # 100 msg/s: gaps get time to expire
        # drain everything remaining via timeout
        while h.buf.backlog:
            h.clock.advance(1.0)
            h.buf.poll(now=h.clock())

        seqs = h.seqs()
        assert_strictly_increasing_mod(self, seqs, bits)
        delivered = set(seqs)
        gapped = {m for g in h.gaps for m in g}
        dropped_late = {s for s, r in h.drops if r == "duplicate"}

        # no message is both delivered and reported missing
        self.assertEqual(delivered & gapped, set())
        # only genuinely-sent messages are ever delivered
        self.assertLessEqual(delivered, set(sent) - lost)
        # every lost seq is eventually reported as a gap (or is a
        # trailing loss past the last delivered message)
        self.assertLessEqual(lost, gapped | {s for s in lost
                                             if s > max(delivered)})
        # every sent, non-lost seq is delivered, or arrived too late
        # (after its position was skipped) and was dropped + reported
        late = (set(sent) - lost) - delivered
        self.assertLessEqual(late, gapped)
        self.assertLessEqual(late, dropped_late)
        # bounded jitter stays inside the window: no overflow drops
        self.assertEqual(h.buf.overflow_drop_count, 0)
        # memory bound never exceeded
        self.assertLessEqual(h.buf.max_backlog, window)


if __name__ == "__main__":
    unittest.main()
