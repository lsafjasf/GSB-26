"""Validation of joint multi-source estimation (timeline_align.joint).

Scenario: four devices A (reference), B, C, D with known offsets and drifts.
Common events form a non-star topology: besides events seen by all devices,
some events are shared only by B-C, only by C-D, and only by A-D, so the
joint system uses information the pairwise estimator cannot. The tests
verify:

1. joint estimates recover ground truth within tolerance;
2. every parameter carries a plausible non-zero uncertainty;
3. joint estimates are consistent with pairwise estimates (same reference
   constraint) within combined uncertainties;
4. closure: the B->C map implied by the joint solution matches a direct
   pairwise B-C fit;
5. parameter uncertainties are confirmed by bootstrap resampling;
6. merging with joint alignments preserves every source's samples and
   sorts on the unified reference timeline.
"""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timeline_align import (
    Alignment,
    Event,
    Source,
    bootstrap_joint,
    estimate_joint,
    estimate_offset_from_common_events,
    merge,
)

T_END = 1_000_000
BIN_WIDTH = 10_000
JITTER = 3

# Ground-truth clock parameters: t_ref = a * t_local + b
TRUE_PARAMS = {
    "A": (1.0, 0.0),
    "B": (1.0 + 2.0e-4, 1500.0),
    "C": (1.0 - 1.5e-4, -800.0),
    "D": (1.0 + 5.0e-5, 4200.0),
}

# Common-event groups: (observers, count). Non-star topology on purpose.
GROUPS = [
    (("A", "B", "C", "D"), 60),
    (("A", "B"), 10),
    (("B", "C"), 15),
    (("C", "D"), 15),
    (("A", "D"), 10),
]


def to_local(t_ref, a, b):
    return round((t_ref - b) / a)


def build_joint_sources(seed=123):
    rng = random.Random(seed)
    events = {name: [] for name in TRUE_PARAMS}
    for observers, count in GROUPS:
        for i in range(count):
            t_ref = rng.randrange(10_000, T_END - 10_000)
            key = f"{'-'.join(observers)}:{i}"
            for name in observers:
                a, b = TRUE_PARAMS[name]
                events[name].append(
                    Event(t=to_local(t_ref, a, b) + rng.randint(-JITTER, JITTER),
                          key=key))
    # Private events at device-specific rates (no keys, different sampling).
    for name, step in (("A", 5_000), ("B", 2_000), ("C", 8_000), ("D", 3_000)):
        a, b = TRUE_PARAMS[name]
        t_ref = rng.randrange(0, step)
        while t_ref < T_END:
            events[name].append(Event(t=to_local(t_ref, a, b),
                                      value=f"{name}@{t_ref}"))
            t_ref += int(rng.expovariate(1.0 / step))
    return {name: Source(name=name, events=events[name]) for name in TRUE_PARAMS}


def pairwise_all(sources):
    out = {"A": Alignment.identity()}
    for name in ("B", "C", "D"):
        out[name] = estimate_offset_from_common_events(sources["A"],
                                                       sources[name])
    return out


class TestJointEstimation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = build_joint_sources()
        cls.joint = estimate_joint(list(cls.sources.values()), ref_name="A")
        cls.pairwise = pairwise_all(cls.sources)

    def test_reference_is_identity(self):
        ref = self.joint["A"]
        self.assertEqual((ref.a, ref.b), (1.0, 0.0))
        self.assertEqual(ref.offset_uncertainty, 0.0)

    def test_joint_recovers_ground_truth(self):
        for name in ("B", "C", "D"):
            est = self.joint[name]
            true_a, true_b = TRUE_PARAMS[name]
            self.assertAlmostEqual(est.offset, true_b, delta=5.0,
                                   msg=f"{name}: offset {est.offset} vs {true_b}")
            self.assertAlmostEqual(est.a, true_a, delta=5e-6,
                                   msg=f"{name}: a {est.a} vs {true_a}")

    def test_joint_uncertainties_plausible(self):
        for name in ("B", "C", "D"):
            est = self.joint[name]
            self.assertGreater(est.offset_uncertainty, 0.0)
            self.assertLess(est.offset_uncertainty, 5.0)
            self.assertGreater(est.drift_uncertainty, 0.0)
            self.assertLess(est.drift_uncertainty, 5e-6)
            self.assertGreater(est.residual_std, 0.5)
            self.assertLess(est.residual_std, 4.0)
            self.assertGreater(est.n_samples, 0)

    def test_joint_consistent_with_pairwise(self):
        # Same reference constraint -> joint and pairwise estimates of the
        # same parameter must agree within combined uncertainties.
        for name in ("B", "C", "D"):
            j, p = self.joint[name], self.pairwise[name]
            comb_off = 4.0 * math.hypot(j.offset_uncertainty,
                                        p.offset_uncertainty)
            comb_drift = 4.0 * math.hypot(j.drift_uncertainty,
                                          p.drift_uncertainty)
            self.assertLessEqual(abs(j.offset - p.offset), comb_off,
                                 msg=f"{name}: offset {j.offset} vs {p.offset}")
            self.assertLessEqual(abs(j.drift - p.drift), comb_drift,
                                 msg=f"{name}: drift {j.drift} vs {p.drift}")

    def test_closure_bc_transform(self):
        # The B->C map implied by the joint solution must match a direct
        # pairwise B-C fit (consistency under composition).
        direct = estimate_offset_from_common_events(self.sources["B"],
                                                    self.sources["C"])
        jb, jc = self.joint["B"], self.joint["C"]
        # t_B = (a_C * t_C + b_C - b_B) / a_B implied by the joint fit.
        a_imp = jc.a / jb.a
        b_imp = (jc.b - jb.b) / jb.a
        ts = [e.t for e in self.sources["C"].events if e.key]
        for t in (min(ts), ts[len(ts) // 2], max(ts)):
            self.assertAlmostEqual(a_imp * t + b_imp, direct.a * t + direct.b,
                                   delta=6.0,
                                   msg=f"closure mismatch at t={t}")

    def test_joint_beats_or_matches_pairwise_on_bc_only_info(self):
        # Sanity: joint fit uses B-C-only events, so its residual std over
        # B-C common events should be no worse than the pairwise fit's.
        def bc_residual_std(alignments):
            res = []
            b_by_key = {e.key: e.t for e in self.sources["B"].events if e.key}
            for e in self.sources["C"].events:
                if e.key and e.key in b_by_key:
                    tb = alignments["B"].to_ref(b_by_key[e.key])
                    tc = alignments["C"].to_ref(e.t)
                    res.append(tb - tc)
            return math.sqrt(sum(r * r for r in res) / len(res))
        self.assertLessEqual(bc_residual_std(self.joint),
                             bc_residual_std(self.pairwise) + 1e-9)

    def test_underdetermined_raises(self):
        lonely = Source("E", [Event(t=i * 1000, value=i) for i in range(50)])
        with self.assertRaises(ValueError):
            estimate_joint([self.sources["A"], self.sources["B"], lonely])
        with self.assertRaises(ValueError):
            estimate_joint([self.sources["A"]])
        with self.assertRaises(ValueError):
            estimate_joint([self.sources["A"], self.sources["B"]],
                           ref_name="ZZ")


class TestJointBootstrap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = build_joint_sources()
        cls.joint = estimate_joint(list(cls.sources.values()), ref_name="A")
        cls.boot = bootstrap_joint(list(cls.sources.values()), ref_name="A",
                                   n_resamples=300, seed=2024)

    def test_bootstrap_confirms_analytic_uncertainties(self):
        for name in ("B", "C", "D"):
            est, bt = self.joint[name], self.boot[name]
            self.assertEqual(bt["n_failed"], 0.0)
            for analytic, resampled, label in (
                (est.offset_uncertainty, bt["offset_std"], "offset"),
                (est.drift_uncertainty, bt["drift_std"], "drift"),
            ):
                self.assertGreater(resampled, 0.0)
                ratio = resampled / analytic
                self.assertGreater(ratio, 0.5,
                                   msg=f"{name} {label}: bootstrap {resampled} "
                                       f"vs analytic {analytic}")
                self.assertLess(ratio, 2.0,
                                msg=f"{name} {label}: bootstrap {resampled} "
                                    f"vs analytic {analytic}")

    def test_bootstrap_means_align_with_estimates(self):
        for name in ("B", "C", "D"):
            est, bt = self.joint[name], self.boot[name]
            self.assertAlmostEqual(bt["offset_mean"], est.offset, delta=2.0)
            self.assertAlmostEqual(bt["drift_mean"], est.drift, delta=2e-6)


class TestJointMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = build_joint_sources()
        cls.alignments = estimate_joint(list(cls.sources.values()),
                                        ref_name="A")
        cls.merged = merge(list(cls.sources.values()), cls.alignments,
                           bin_width=BIN_WIDTH, ref_name="A")

    def test_no_source_samples_dropped(self):
        counts = self.merged.count_by_source()
        for name, src in self.sources.items():
            self.assertEqual(counts[name], len(src.events),
                             f"source {name}: {counts[name]} != {len(src.events)}")

    def test_sorted_on_unified_timeline(self):
        times = [t for t, _, _ in self.merged.iter_events()]
        self.assertEqual(times, sorted(times))

    def test_all_sources_present_on_timeline(self):
        names = set()
        for _, name, _ in self.merged.iter_events():
            names.add(name)
        self.assertEqual(names, set(self.sources))


if __name__ == "__main__":
    unittest.main(verbosity=2)
