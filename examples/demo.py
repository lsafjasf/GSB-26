"""End-to-end demo: synthetic multi-device data with known clock offsets.

Prints:
  1. offset/drift estimates vs. ground truth (validation of accuracy);
  2. alignment quality metrics (residual distribution after alignment);
  3. a sample of the merged timeline, including MISSING vs. EMPTY buckets;
  4. the merge invariant check (no event dropped or duplicated).

Run:  python3 examples/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from test_timeline_align import (  # noqa: E402
    TRUE_PARAMS, B_GAP_REF, C_QUIET_REF, BIN_WIDTH,
    build_sources, estimate_all,
)
from timeline_align import merge, quality_report  # noqa: E402


def main():
    sources = build_sources()
    alignments = estimate_all(sources)

    print("=" * 74)
    print("1. Offset / drift estimation vs. ground truth (common-events OLS)")
    print("=" * 74)
    print(f"{'src':<4} {'offset est':>12} {'+/-':>7} {'offset true':>12} "
          f"{'drift est (ppm)':>16} {'drift true (ppm)':>17} {'resid std':>10}")
    for name in ("B", "C"):
        est = alignments[name]
        true_a, true_b = TRUE_PARAMS[name]
        print(f"{name:<4} {est.offset:>12.2f} {est.offset_uncertainty:>7.2f} "
              f"{true_b:>12.1f} {est.drift * 1e6:>16.2f} "
              f"{(true_a - 1) * 1e6:>17.2f} {est.residual_std:>10.2f}")

    print()
    print("=" * 74)
    print("2. Alignment quality: residuals of common events after alignment")
    print("=" * 74)
    report = quality_report(sources["A"], list(sources.values()), alignments)
    print(f"{'src':<4} {'n':>5} {'mean':>8} {'std':>8} {'p95_abs':>9} {'max_abs':>9}")
    for name, st in report.items():
        print(f"{name:<4} {st['n']:>5} {st['mean']:>8.2f} {st['std']:>8.2f} "
              f"{st['p95_abs']:>9.2f} {st['max_abs']:>9.2f}")

    merged = merge(list(sources.values()), alignments,
                   bin_width=BIN_WIDTH, ref_name="A")

    print()
    print("=" * 74)
    print("3. Merged timeline around B's recording gap "
          f"{B_GAP_REF} (bin_width={BIN_WIDTH})")
    print("=" * 74)
    k0 = merged.buckets[0].t_start // BIN_WIDTH
    print(f"{'bucket [start,end)':<22} {'A':>8} {'B':>8} {'C':>8}")
    for k in range(B_GAP_REF[0] // BIN_WIDTH - 1, B_GAP_REF[1] // BIN_WIDTH + 2):
        b = merged.buckets[k - k0]
        row = [f"[{b.t_start:>7},{b.t_end:>7})"]
        for name in ("A", "B", "C"):
            cell = b.cells[name]
            label = cell.status if cell.status != "present" \
                else f"present({len(cell.events)})"
            row.append(f"{label:>8}")
        print(" ".join(row))
    print(f"(C's quiet stretch {C_QUIET_REF} shows as 'empty': "
          "recording, but no events)")

    print()
    print("=" * 74)
    print("4. Merge invariant: events in result == events in input")
    print("=" * 74)
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
    print(f"  INVARIANT: {'PASS' if ok and sorted_ok else 'FAIL'}")


if __name__ == "__main__":
    main()
