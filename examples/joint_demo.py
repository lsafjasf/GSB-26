"""End-to-end demo: joint alignment of four sources.

Prints:
  1. joint offset/drift estimates vs. pairwise estimates and ground truth;
  2. analytic uncertainties vs. bootstrap (cluster resampling) uncertainties;
  3. closure check: B->C map implied by the joint fit vs. a direct B-C fit;
  4. merge invariant on the unified reference timeline (no sample dropped).

Run:  python3 examples/joint_demo.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from test_joint import (  # noqa: E402
    TRUE_PARAMS, GROUPS, BIN_WIDTH, build_joint_sources, pairwise_all,
)
from timeline_align import (  # noqa: E402
    bootstrap_joint, estimate_joint,
    estimate_offset_from_common_events, merge,
)


def main():
    sources = build_joint_sources()
    joint = estimate_joint(list(sources.values()), ref_name="A")
    pairwise = pairwise_all(sources)
    boot = bootstrap_joint(list(sources.values()), ref_name="A",
                           n_resamples=300, seed=2024)

    total_common = sum(n for _, n in GROUPS)
    print("=" * 92)
    print(f"JOINT ALIGNMENT OF 4 SOURCES (A=reference); "
          f"{total_common} common events, non-star topology B-C / C-D / A-D")
    print("=" * 92)

    print()
    print("1. Joint vs. pairwise vs. ground truth")
    print("-" * 92)
    print(f"{'src':<4} {'offset joint':>13} {'pairwise':>10} {'true':>8} "
          f"{'|J-P|':>7} | {'drift(ppm) joint':>17} {'pairwise':>10} "
          f"{'true':>8} {'|J-P|':>8}")
    for name in ("B", "C", "D"):
        j, p = joint[name], pairwise[name]
        true_a, true_b = TRUE_PARAMS[name]
        print(f"{name:<4} {j.offset:>13.2f} {p.offset:>10.2f} {true_b:>8.1f} "
              f"{abs(j.offset - p.offset):>7.2f} | "
              f"{j.drift * 1e6:>17.2f} {p.drift * 1e6:>10.2f} "
              f"{(true_a - 1) * 1e6:>8.2f} {abs(j.drift - p.drift) * 1e6:>8.3f}")

    print()
    print("2. Parameter uncertainty: analytic (joint covariance) vs. bootstrap")
    print("-" * 92)
    print(f"{'src':<4} {'offset se':>10} {'bootstrap sd':>13} {'ratio':>6} "
          f"| {'drift se(ppm)':>14} {'bootstrap sd':>13} {'ratio':>6} "
                  f"{'resid_std':>10} {'n_obs':>6}")
    for name in ("B", "C", "D"):
        j, bt = joint[name], boot[name]
        print(f"{name:<4} {j.offset_uncertainty:>10.3f} "
              f"{bt['offset_std']:>13.3f} "
              f"{bt['offset_std'] / j.offset_uncertainty:>6.2f} | "
              f"{j.drift_uncertainty * 1e6:>14.3f} "
              f"{bt['drift_std'] * 1e6:>13.3f} "
              f"{bt['drift_std'] / j.drift_uncertainty:>6.2f} "
              f"{j.residual_std:>10.2f} {j.n_samples:>6}")
    print(f"(bootstrap: {int(boot['B']['n_resamples'])} event-cluster "
          f"resamples; failed draws = {int(boot['B']['n_failed'])})")

    print()
    print("3. Closure consistency: B->C map implied by joint vs. direct fit")
    print("-" * 92)
    direct = estimate_offset_from_common_events(sources["B"], sources["C"])
    jb, jc = joint["B"], joint["C"]
    a_imp = jc.a / jb.a
    b_imp = (jc.b - jb.b) / jb.a
    ts = [e.t for e in sources["C"].events if e.key]
    print(f"{'t_C':>10} {'joint B-time':>14} {'pairwise B-time':>16} {'diff':>8}")
    for t in (min(ts), ts[len(ts) // 2], max(ts)):
        t_imp = a_imp * t + b_imp
        t_dir = direct.a * t + direct.b
        print(f"{t:>10} {t_imp:>14.2f} {t_dir:>16.2f} "
              f"{t_imp - t_dir:>8.2f}")

    print()
    print("4. Merge on unified timeline: every sample preserved and sorted")
    print("-" * 92)
    merged = merge(list(sources.values()), joint,
                   bin_width=BIN_WIDTH, ref_name="A")
    counts = merged.count_by_source()
    ok = True
    for name, src in sources.items():
        match = counts[name] == len(src.events)
        ok &= match
        print(f"  {name}: input={len(src.events):>5}  merged={counts[name]:>5}  "
              f"{'OK' if match else 'MISMATCH'}")
    times = [t for t, _, _ in merged.iter_events()]
    sorted_ok = times == sorted(times)
    print(f"  globally sorted by aligned time: {'OK' if sorted_ok else 'FAIL'}")
    print(f"  total events: input={sum(len(s.events) for s in sources.values())}"
          f"  merged={len(times)}")
    print(f"  buckets: {len(merged.buckets)} "
          f"[{merged.buckets[0].t_start}, {merged.buckets[-1].t_end})")
    print(f"  INVARIANT: {'PASS' if ok and sorted_ok else 'FAIL'}")


if __name__ == "__main__":
    main()
