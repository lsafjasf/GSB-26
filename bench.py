"""Performance benchmark for tplcheck.

Usage: python3 bench.py [num_templates]
"""

import random
import statistics
import sys
import time

from tplcheck import compare, render, validate
from tests.tplgen import TemplateGenerator, gen_args


def percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * pct))
    return sorted_vals[idx]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rng = random.Random(20260927)
    gen = TemplateGenerator(rng, max_depth=5)

    # mix of small / medium / large templates
    templates = []
    for i in range(n):
        if i % 10 == 9:
            src = gen.generate(min_nodes=30, max_nodes=60)  # large
        elif i % 10 >= 6:
            src = gen.generate(min_nodes=10, max_nodes=25)  # medium
        else:
            src = gen.generate(min_nodes=1, max_nodes=10)  # small
        templates.append(src)

    total_chars = sum(len(t) for t in templates)

    # --- validation throughput ------------------------------------------------
    results = []
    times = []
    t0 = time.perf_counter()
    for src in templates:
        s = time.perf_counter()
        results.append(validate(src))
        times.append(time.perf_counter() - s)
    total = time.perf_counter() - t0
    times_ms = sorted(t * 1e3 for t in times)

    ok_count = sum(1 for r in results if r.ok)
    print(f"templates:            {n} ({total_chars} chars total)")
    print(f"valid:                {ok_count}, invalid: {n - ok_count}")
    print(f"validate total:       {total * 1e3:.1f} ms")
    print(f"validate per tpl:     mean {statistics.mean(times_ms):.3f} ms, "
          f"p50 {percentile(times_ms, 0.50):.3f} ms, "
          f"p95 {percentile(times_ms, 0.95):.3f} ms, "
          f"max {times_ms[-1]:.3f} ms")
    print(f"validate throughput:  {n / total:,.0f} templates/s")

    # --- cross-language compare ------------------------------------------------
    groups = [
        {lang: templates[i] for lang in ("en", "zh", "ja", "de")}
        for i in range(0, min(n, 400), 4)
    ]
    t0 = time.perf_counter()
    reports = [compare(g) for g in groups]
    cmp_total = time.perf_counter() - t0
    print(f"compare 4 langs:      {len(groups)} groups in "
          f"{cmp_total * 1e3:.1f} ms "
          f"({cmp_total / len(groups) * 1e3:.3f} ms/group)")

    # --- render (for reference) -------------------------------------------------
    rng2 = random.Random(1)
    renderable = [r for r in results if r.ok][:500]
    t0 = time.perf_counter()
    for r in renderable:
        render(r.ast, gen_args(r, rng2))
    rnd_total = time.perf_counter() - t0
    print(f"render:               {len(renderable)} templates in "
          f"{rnd_total * 1e3:.1f} ms "
          f"({rnd_total / len(renderable) * 1e3:.3f} ms/tpl)")


if __name__ == "__main__":
    main()
