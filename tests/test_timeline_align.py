"""Synthetic-data validation and invariant tests for timeline_align.

Scenario: three devices record the same physical process on their own
clocks. Device clocks have known fixed offsets and slight linear drift
relative to device A (the reference). Each device records:

* common events (observed by all devices, matched by key, with per-device
  timestamp jitter), and
* private events at device-specific rates (different sampling frequencies).

Device B has a recording gap (missing data); device C has a quiet stretch
(recording, but no events). The tests verify:

1. offset/drift estimates recover the known ground truth within tolerance;
2. the cross-correlation estimator recovers a known pure offset;
3. the merge invariant: every source's events appear in the merged result
   exactly as many times as in the input (nothing dropped, nothing duplicated);
4. merged events are sorted by aligned time;
5. missing intervals are marked MISSING and are distinguishable from EMPTY.
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timeline_align import (
    Alignment,
    Event,
    Source,
    estimate_offset_from_common_events,
    estimate_offset_xcorr,
    merge,
    quality_report,
    PRESENT,
    EMPTY,
    MISSING,
)

T_END = 1_000_000
BIN_WIDTH = 10_000

# Ground-truth clock parameters: t_ref = a * t_local + b
TRUE_PARAMS = {
    "A": (1.0, 0.0),
    "B": (1.0 + 2.0e-4, 1500.0),
    "C": (1.0 - 1.5e-4, -800.0),
}

B_GAP_REF = (300_000, 400_000)      # B not recording (reference clock)
C_QUIET_REF = (600_000, 650_000)    # C recording but no events (reference clock)

N_COMMON = 80
JITTER = 3


def to_local(t_ref, a, b):
    return round((t_ref - b) / a)


def build_sources(seed=42):
    rng = random.Random(seed)
    events = {name: [] for name in TRUE_PARAMS}

    # Common events observed by every device (with per-device jitter).
    # None are generated inside C's quiet stretch: nothing happens then.
    for i in range(N_COMMON):
        while True:
            t_ref = rng.randrange(10_000, T_END - 10_000)
            if not (C_QUIET_REF[0] <= t_ref < C_QUIET_REF[1]):
                break
        for name, (a, b) in TRUE_PARAMS.items():
            # B is powered off during its gap: it misses common events too.
            if name == "B" and B_GAP_REF[0] <= t_ref < B_GAP_REF[1]:
                continue
            t_local = to_local(t_ref, a, b) + rng.randint(-JITTER, JITTER)
            events[name].append(Event(t=t_local, key=f"c{i}", value=i))

    # Private events at device-specific rates (different sampling frequencies).
    for name, step in (("A", 5_000), ("B", 2_000), ("C", 8_000)):
        a, b = TRUE_PARAMS[name]
        t_ref = rng.randrange(0, step)
        while t_ref < T_END:
            # B's gap: device is off, nothing is recorded.
            if not (name == "B" and B_GAP_REF[0] <= t_ref < B_GAP_REF[1]):
                # C's quiet stretch: device is on, nothing happens.
                if not (name == "C" and C_QUIET_REF[0] <= t_ref < C_QUIET_REF[1]):
                    events[name].append(Event(t=to_local(t_ref, a, b),
                                              value=f"{name}@{t_ref}"))
            t_ref += int(rng.expovariate(1.0 / step))

    sources = {}
    for name, (a, b) in TRUE_PARAMS.items():
        coverage = None
        if name == "B":
            gap_lo = to_local(B_GAP_REF[0], a, b)
            gap_hi = to_local(B_GAP_REF[1], a, b)
            lo = min(e.t for e in events[name])
            hi = max(e.t for e in events[name])
            coverage = [(lo, gap_lo - 1), (gap_hi + 1, hi)]
        sources[name] = Source(name=name, events=events[name], coverage=coverage)
    return sources


def estimate_all(sources):
    alignments = {"A": Alignment.identity()}
    for name in ("B", "C"):
        alignments[name] = estimate_offset_from_common_events(
            sources["A"], sources[name])
    return alignments


class TestOffsetEstimation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = build_sources()
        cls.alignments = estimate_all(cls.sources)

    def test_offset_recovers_ground_truth(self):
        for name in ("B", "C"):
            est = self.alignments[name]
            true_b = TRUE_PARAMS[name][1]
            self.assertAlmostEqual(est.offset, true_b, delta=5.0,
                                   msg=f"{name}: offset {est.offset} vs {true_b}")
            # Reported uncertainty should be plausible (non-zero, small).
            self.assertGreater(est.offset_uncertainty, 0.0)
            self.assertLess(est.offset_uncertainty, 5.0)

    def test_drift_recovers_ground_truth(self):
        for name in ("B", "C"):
            est = self.alignments[name]
            true_a = TRUE_PARAMS[name][0]
            self.assertAlmostEqual(est.a, true_a, delta=5e-6,
                                   msg=f"{name}: a {est.a} vs {true_a}")

    def test_residual_std_matches_jitter(self):
        # Jitter is uniform(-3, 3) on both clocks: std of the difference is
        # sqrt(2) * 1.73 ~= 2.45. The fit residual std should be in that range.
        for name in ("B", "C"):
            est = self.alignments[name]
            self.assertGreater(est.residual_std, 1.0)
            self.assertLess(est.residual_std, 4.0)

    def test_xcorr_recovers_pure_offset(self):
        # Separate scenario: no drift, known offset, no shared keys.
        rng = random.Random(7)
        true_offset = 2500
        ref_events, src_events = [], []
        for i in range(120):
            t = rng.randrange(0, 200_000)
            ref_events.append(Event(t=t))
            src_events.append(Event(t=t - true_offset + rng.randint(-2, 2)))
        # Uncorrelated background noise.
        for _ in range(150):
            ref_events.append(Event(t=rng.randrange(0, 200_000)))
            src_events.append(Event(t=rng.randrange(-200_000, 0)))
        ref = Source("r", ref_events)
        src = Source("s", src_events)
        est = estimate_offset_xcorr(ref, src, max_lag=10_000, bin_width=10)
        self.assertAlmostEqual(est.offset, true_offset, delta=10.0)
        self.assertGreater(est.offset_uncertainty, 0.0)

    def test_xcorr_raises_without_pairs(self):
        ref = Source("r", [Event(t=0)])
        src = Source("s", [Event(t=10**9)])
        with self.assertRaises(ValueError):
            estimate_offset_xcorr(ref, src, max_lag=100)

    def test_common_events_requires_two(self):
        ref = Source("r", [Event(t=0, key="k")])
        src = Source("s", [Event(t=5, key="k")])
        with self.assertRaises(ValueError):
            estimate_offset_from_common_events(ref, src)


class TestMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = build_sources()
        cls.alignments = estimate_all(cls.sources)
        cls.merged = merge(list(cls.sources.values()), cls.alignments,
                           bin_width=BIN_WIDTH, ref_name="A")

    def test_invariant_event_counts_preserved(self):
        counts = self.merged.count_by_source()
        for name, src in self.sources.items():
            self.assertEqual(counts[name], len(src.events),
                             f"source {name}: {counts[name]} != {len(src.events)}")

    def test_invariant_total_events_preserved(self):
        total_in = sum(len(s.events) for s in self.sources.values())
        total_out = sum(1 for _ in self.merged.iter_events())
        self.assertEqual(total_in, total_out)

    def test_events_sorted_by_aligned_time(self):
        times = [t for t, _, _ in self.merged.iter_events()]
        self.assertEqual(times, sorted(times))

    def test_buckets_tile_without_overlap(self):
        buckets = self.merged.buckets
        for prev, nxt in zip(buckets, buckets[1:]):
            self.assertEqual(prev.t_end, nxt.t_start)
            self.assertEqual(nxt.t_end - nxt.t_start, BIN_WIDTH)

    def test_missing_interval_distinguished_from_empty(self):
        # Bucket fully inside B's recording gap -> MISSING for B.
        k = (B_GAP_REF[0] + 50_000) // BIN_WIDTH
        idx = k - self.merged.buckets[0].t_start // BIN_WIDTH
        self.assertEqual(self.merged.buckets[idx].cells["B"].status, MISSING)
        # Other sources are unaffected by B's gap.
        self.assertIn(self.merged.buckets[idx].cells["A"].status, (PRESENT, EMPTY))

        # Bucket fully inside C's quiet stretch -> EMPTY for C (recording,
        # no events), which must differ from MISSING.
        k2 = (C_QUIET_REF[0] + 20_000) // BIN_WIDTH
        idx2 = k2 - self.merged.buckets[0].t_start // BIN_WIDTH
        self.assertEqual(self.merged.buckets[idx2].cells["C"].status, EMPTY)

    def test_present_status(self):
        # At least one bucket has events from every source's densest stream.
        statuses = self.merged.status_grid()
        self.assertIn(PRESENT, statuses["B"])

    def test_merge_requires_all_alignments(self):
        with self.assertRaises(KeyError):
            merge(list(self.sources.values()), {"A": Alignment.identity()},
                  bin_width=BIN_WIDTH)


class TestQualityReport(unittest.TestCase):
    def test_quality_report(self):
        sources = build_sources()
        alignments = estimate_all(sources)
        report = quality_report(sources["A"], list(sources.values()), alignments)
        self.assertEqual(set(report), {"B", "C"})
        for name, stats in report.items():
            shared = {e.key for e in sources["A"].events if e.key} & \
                     {e.key for e in sources[name].events if e.key}
            self.assertEqual(stats["n"], len(shared))
            self.assertLess(stats["std"], 4.0)
            self.assertLess(stats["max_abs"], 15.0)
            self.assertLessEqual(stats["p95_abs"], stats["max_abs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
