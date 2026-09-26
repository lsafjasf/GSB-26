"""Coverage comparison: structured fuzzing vs random baselines.

Three arms, each fed the same number of inputs into sut.run:
  1. random-bytes : raw random bytes (classic dumb fuzzing)
  2. random-json  : valid JSON values with random shape (no schema)
  3. structured   : fuzzgen combinators (50% valid / 50% business-violating)

Usage: python3 compare.py [N] [seed]
"""

import json
import random
import sys

import fuzzgen as fg
import sut
from schema import BATCH


def arm_random_bytes(seed, n):
    rng = random.Random(seed)
    return [rng.randbytes(rng.randint(0, 256)) for _ in range(n)]


def arm_random_json(seed, n):
    rng = random.Random(seed)

    def val(d):
        r = rng.random()
        if d > 3 or r < 0.4:
            return rng.choice([0, 1, -1, 3.14, "x", True, None, "a" * 50])
        if r < 0.7:
            return [val(d + 1) for _ in range(rng.randint(0, 4))]
        return {rng.choice("abcdef") * rng.randint(1, 3): val(d + 1)
                for _ in range(rng.randint(0, 4))}

    return [json.dumps(val(0)).encode() for _ in range(n)]


def gen_structured(seed, n, violate_ratio=0.5):
    """Single RNG stream -> fully reproducible sequence for (seed, n)."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        violate = rng.random() < violate_ratio
        # violate mode gets a bigger depth budget: exceeds the SUT's
        # business depth limit (4) but is still hard-capped -> terminates.
        ctx = fg.Ctx(rng, max_depth=8 if violate else 3, violate=violate)
        out.append(BATCH.gen(ctx))
    return out


def arm_structured(seed, n):
    return [json.dumps(v, sort_keys=True).encode()
            for v in gen_structured(seed, n)]


def run_arm(inputs):
    sut.COVERAGE.clear()
    accepted = 0
    for raw in inputs:
        try:
            sut.run(raw)
            accepted += 1
        except sut.Reject:
            pass
    return set(sut.COVERAGE), accepted


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260927

    arms = [
        ("random-bytes", arm_random_bytes(seed, n)),
        ("random-json ", arm_random_json(seed, n)),
        ("structured  ", arm_structured(seed, n)),
    ]

    results = {}
    for name, inputs in arms:
        cov, accepted = run_arm(inputs)
        results[name] = cov
        print(f"{name} | inputs={n} | accepted={accepted:5d} "
              f"| distinct branches={len(cov):3d}")

    structured = results["structured  "]
    baseline = results["random-bytes"] | results["random-json "]
    only = sorted(structured - baseline)
    print(f"\nbranches reached ONLY by structured fuzzing ({len(only)}):")
    for tag in only:
        print(f"  {tag}")
    missing = sorted(baseline - structured)
    print(f"branches reached by baselines but not structured: "
          f"{missing if missing else 'none'}")


if __name__ == "__main__":
    main()
