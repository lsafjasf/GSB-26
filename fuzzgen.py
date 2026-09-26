"""Structure-aware fuzz input generation framework (Python stdlib only).

Combinators generate *structurally legal* inputs driven by a seeded RNG, so
any (seed, count) pair reproduces the exact same input sequence.

Boundary bias: leaf generators (Int/Text/ListOf) pick from a pool of
interesting values (min, max, empty, zero, +/-1, oversized, deep nesting)
with a configurable weight, instead of uniform sampling.

Violation mode: when Ctx.violate is set, leaf generators may emit values
that break *business* constraints (out-of-range ints, oversized strings,
over-long lists, too-deep nesting) while keeping every *structural* rule
intact (types, required fields, list nesting, valid JSON). Structure stays
legal because only leaf *values* are perturbed; the shape of the value is
fully determined by the combinator tree, which never changes.

Termination: Recursive carries a hard depth budget (Ctx.max_depth). Once
the budget is exhausted only the non-recursive base generator is used, so
generation always terminates regardless of recurse_weight.
"""

import random
import string


class Ctx:
    """Per-run generation context: RNG stream + recursion budget + mode."""

    __slots__ = ("rng", "depth", "max_depth", "violate")

    def __init__(self, rng, max_depth=6, violate=False):
        self.rng = rng
        self.depth = 0
        self.max_depth = max_depth
        self.violate = violate


class Gen:
    def gen(self, ctx):
        raise NotImplementedError

    def generate(self, seed, max_depth=6, violate=False):
        """Convenience: generate a single value from an integer seed."""
        return self.gen(Ctx(random.Random(seed), max_depth, violate))


class Const(Gen):
    def __init__(self, value):
        self.value = value

    def gen(self, ctx):
        return self.value


class Int(Gen):
    """Integer in [lo, hi], weighted toward boundary values.

    Valid mode: always within [lo, hi].
    Violate mode: may emit out-of-range values (still a JSON int).
    """

    def __init__(self, lo, hi, boundary_weight=0.4):
        assert lo <= hi
        self.lo = lo
        self.hi = hi
        self.boundary_weight = boundary_weight

    def gen(self, ctx):
        rng = ctx.rng
        if ctx.violate:
            pool = [self.lo, self.hi, 0, 1, -1, self.lo - 1, self.hi + 1]
        else:
            pool = [v for v in (self.lo, self.hi, 0, 1, -1)
                    if self.lo <= v <= self.hi]
        if rng.random() < self.boundary_weight:
            return rng.choice(pool)
        if ctx.violate and rng.random() < 0.3:
            mag = rng.randint(self.hi + 1, self.hi * 100 + 1)
            return mag * rng.choice([1, -1])
        return rng.randint(self.lo, self.hi)


class Text(Gen):
    """String with length in [min_len, max_len], weighted toward boundaries.

    Violate mode: may emit empty (when min_len > 0) or oversized strings
    (still a JSON string).
    """

    DEFAULT_ALPHABET = string.ascii_letters + string.digits

    def __init__(self, min_len=0, max_len=16, alphabet=None,
                 boundary_weight=0.4):
        self.min_len = min_len
        self.max_len = max_len
        self.alphabet = alphabet or self.DEFAULT_ALPHABET
        self.boundary_weight = boundary_weight

    def gen(self, ctx):
        rng = ctx.rng
        if rng.random() < self.boundary_weight:
            choices = [self.min_len, self.max_len]
            if ctx.violate:
                choices += [self.max_len + 1, self.max_len * 8 + 1]
                if self.min_len > 0:
                    choices.append(0)
            n = rng.choice(choices)
        else:
            n = rng.randint(self.min_len, self.max_len)
        return "".join(rng.choice(self.alphabet) for _ in range(n))


class Bool(Gen):
    def gen(self, ctx):
        return ctx.rng.random() < 0.5


class Choice(Gen):
    """Weighted choice among alternative generators."""

    def __init__(self, weighted):
        self.weighted = list(weighted)
        assert self.weighted and all(w > 0 for w, _ in self.weighted)
        self.total = sum(w for w, _ in self.weighted)

    def gen(self, ctx):
        r = ctx.rng.random() * self.total
        for w, g in self.weighted:
            r -= w
            if r <= 0:
                return g.gen(ctx)
        return self.weighted[-1][1].gen(ctx)


class Record(Gen):
    """Fixed-shape object: {field: Gen}. Optional deterministic post hook
    (e.g. to repair business invariants in valid mode)."""

    def __init__(self, fields, post=None):
        self.fields = dict(fields)
        self.post = post

    def gen(self, ctx):
        rec = {k: g.gen(ctx) for k, g in self.fields.items()}
        if self.post is not None:
            rec = self.post(rec, ctx)
        return rec


class ListOf(Gen):
    """List with length in [min_len, max_len], weighted toward boundaries.

    Violate mode: may emit over-long lists (still a JSON array).
    """

    def __init__(self, item, min_len=0, max_len=5, boundary_weight=0.3):
        self.item = item
        self.min_len = min_len
        self.max_len = max_len
        self.boundary_weight = boundary_weight

    def gen(self, ctx):
        rng = ctx.rng
        if rng.random() < self.boundary_weight:
            choices = [self.min_len, self.max_len]
            if ctx.violate:
                choices += [self.max_len + 1, self.max_len * 4 + 1]
            n = rng.choice(choices)
        else:
            n = rng.randint(self.min_len, self.max_len)
        return [self.item.gen(ctx) for _ in range(n)]


class Recursive(Gen):
    """Recursive structure with a hard depth budget (no infinite recursion).

    base:   non-recursive Gen used when the budget is exhausted.
    extend: fn(recursive_gen) -> Gen wrapping one more level around it.
    recurse_weight: probability of recursing while budget remains.
    """

    def __init__(self, base, extend, recurse_weight=0.6):
        self.base = base
        self.extend = extend
        self.recurse_weight = recurse_weight

    def gen(self, ctx):
        if ctx.depth >= ctx.max_depth or ctx.rng.random() >= self.recurse_weight:
            return self.base.gen(ctx)
        ctx.depth += 1
        try:
            return self.extend(self).gen(ctx)
        finally:
            ctx.depth -= 1
