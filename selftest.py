#!/usr/bin/env python3
"""Framework self-test.

Runs muttest against examples/billing.py + examples/test_billing.py, where the
expected classification of every single mutant is known a priori:

  * TIMEOUT    -- the two mutants that make count_down() loop forever
  * SURVIVED   -- the five mutants of the deliberately untested guard in
                  discount() (`if percent > 100: raise`)
  * EQUIVALENT -- the seven manually confirmed equivalent mutants listed in
                  equivalents.json (excluded from the score denominator)
  * KILLED     -- everything else

The self-test fails (exit 1) on any misclassification. It also verifies the
score arithmetic and the incremental cache (a second run must perform zero
subprocess executions and produce identical results).
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from muttest.runner import (INCOMPETENT, KILLED, SURVIVED, TIMEOUT,
                            run_suite)

ROOT = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(ROOT, "examples", "billing.py")
TESTS = os.path.join(ROOT, "examples", "test_billing.py")
EQUIVALENTS = os.path.join(ROOT, "equivalents.json")
TIMEOUT_S = 3.0


def line_of(source, needle):
    for i, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"line not found: {needle!r}")


def build_expectations():
    with open(MODULE) as fh:
        source = fh.read()
    with open(EQUIVALENTS) as fh:
        equivalents = json.load(fh)

    while_line = line_of(source, "while n > 0:")
    guard_if = line_of(source, "if percent > 100:")
    guard_raise = line_of(source, "raise ValueError")
    decrement = line_of(source, "n -= 1")

    expected = {}  # predicate -> status, evaluated per mutant
    timeouts, survivors = set(), set()

    def expect(m):
        """Compute the expected status for a mutant."""
        # count_down: `n -= 1` -> `n -= 0` loops forever; deleting the
        # decrement loops forever too; `while n > 0` -> `while n <= 0`
        # loops forever once entered (n keeps decreasing below 0).
        if m.lineno == decrement and (
                m.detail == "1 -> 0" or m.kind == "statement_deletion"):
            return TIMEOUT
        if m.lineno == while_line and m.kind == "conditional_negation":
            return TIMEOUT
        # discount: the guard is never exercised by the weak test suite, so
        # mutants that only weaken/remove it survive. The conditional
        # negation `> -> <=` fires on percent=50 and is killed instead.
        if m.lineno in (guard_if, guard_raise) and not (
                m.lineno == guard_if and m.kind == "conditional_negation"):
            return SURVIVED
        return KILLED

    return equivalents, expect


def check(summary, equivalents, expect, label):
    failures = []
    for r in summary.results:
        m = r.mutant
        if m.id in equivalents:
            continue  # equivalent mutants may have any status; excluded anyway
        want = expect(m)
        if r.status != want:
            failures.append(f"  MISJUDGED {m.id} line {m.lineno} "
                            f"{m.detail!r}: got {r.status}, want {want}")
    # every equivalent id must correspond to a real mutant
    known = {r.mutant.id for r in summary.results}
    for mid in equivalents:
        if mid not in known:
            failures.append(f"  equivalent id not found among mutants: {mid}")
    # score arithmetic
    score, killed, denominator = summary.score()
    eq = len(summary.equivalents)
    inc = len(summary.by_status(INCOMPETENT))
    if denominator != summary.total - eq - inc:
        failures.append("  score denominator mismatch")
    if inc != 0:
        failures.append(f"  unexpected incompetent mutants: {inc}")
    if failures:
        print(f"[{label}] FAILED:")
        print("\n".join(failures))
        return False
    print(f"[{label}] OK: {summary.total} mutants, "
          f"{len(summary.by_status(KILLED))} killed, "
          f"{len(summary.by_status(SURVIVED))} survived, "
          f"{len(summary.by_status(TIMEOUT))} timeout, "
          f"{eq} equivalent (excluded); score {score:.1f}% "
          f"({killed}/{denominator})")
    return True


def main():
    equivalents, expect = build_expectations()
    jobs = max(2, (os.cpu_count() or 2) // 2)
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache.json")

        # 1) cold run: every mutant is executed in a subprocess
        start = time.monotonic()
        cold = run_suite(MODULE, TESTS, timeout=TIMEOUT_S, jobs=jobs,
                         cache_path=cache, equivalents=equivalents)
        cold_wall = time.monotonic() - start
        ok &= check(cold, equivalents, expect, "cold run")
        assert cold.executions == cold.total, "cold run must execute all mutants"
        assert cold.cache_hits == 0

        # 2) warm run: incremental cache must serve everything
        start = time.monotonic()
        warm = run_suite(MODULE, TESTS, timeout=TIMEOUT_S, jobs=jobs,
                         cache_path=cache, equivalents=equivalents)
        warm_wall = time.monotonic() - start
        ok &= check(warm, equivalents, expect, "warm run (cache)")
        assert warm.executions == 0, "warm run must execute nothing"
        assert warm.cache_hits == warm.total
        assert [r.status for r in warm.results] == \
               [r.status for r in cold.results], "cache changed results"

    print()
    print("PERFORMANCE")
    print(f"  mutants generated : {cold.total}")
    print(f"  cold run          : {cold.executions} executions, "
          f"{cold_wall:.2f}s wall (jobs={jobs}, timeout={TIMEOUT_S}s)")
    print(f"  warm run          : {warm.executions} executions "
          f"({warm.cache_hits} cache hits), {warm_wall:.2f}s wall")
    print(f"  incremental speedup: {cold_wall / max(warm_wall, 1e-9):.0f}x")
    print()
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
