#!/usr/bin/env python3
"""Self-test suite for probdist. Prints a full report; exits 1 on failure.

Run: python3 selftest.py
"""

import random
import sys
import traceback

from probdist import distributions as dist
from probdist import fit, gof

FAILURES = []


def check(name, cond, extra=""):
    status = "ok" if cond else "FAILED"
    print(f"  [{status}] {name}" + (f" -- {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


def expect_raises(name, fn, exc=ValueError):
    try:
        fn()
    except exc as e:
        check(name, True, f"raised {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"wrong exception {type(e).__name__}: {e}")
    else:
        check(name, False, "no exception raised")


def section(title):
    print(f"\n=== {title} ===")


# ------------------------------------------------------- 1. reproducibility
section("1. Reproducibility: same seed => identical samples")
for name, fn in [
    ("uniform", lambda r: dist.sample_uniform(r, -2, 5, 1000)),
    ("exponential", lambda r: dist.sample_exponential(r, 1.5, 1000)),
    ("normal", lambda r: dist.sample_normal(r, 10, 2, 1000)),
    ("binomial", lambda r: dist.sample_binomial(r, 20, 0.3, 1000)),
]:
    a = fn(random.Random(42))
    b = fn(random.Random(42))
    c = fn(random.Random(43))
    check(f"{name}: same seed identical", a == b)
    check(f"{name}: different seed differs", a != c)

# ------------------------------------------------------------ 2. edge cases
section("2. Edge cases: n=0 and invalid parameters")
check("uniform n=0 returns []", dist.sample_uniform(random.Random(1), 0, 1, 0) == [])
check("normal n=0 returns []", dist.sample_normal(random.Random(1), 0, 1, 0) == [])
expect_raises("uniform low>=high rejected",
              lambda: dist.sample_uniform(random.Random(1), 3, 3, 1))
expect_raises("exponential lam<=0 rejected",
              lambda: dist.sample_exponential(random.Random(1), 0.0, 1))
expect_raises("exponential negative lam rejected",
              lambda: dist.sample_exponential(random.Random(1), -2, 1))
expect_raises("normal sigma<=0 rejected",
              lambda: dist.sample_normal(random.Random(1), 0, -1, 1))
expect_raises("binomial p out of range rejected",
              lambda: dist.sample_binomial(random.Random(1), 10, 1.5, 1))
expect_raises("binomial negative trials rejected",
              lambda: dist.sample_binomial(random.Random(1), -3, 0.5, 1))
expect_raises("negative n rejected",
              lambda: dist.sample_uniform(random.Random(1), 0, 1, -5))
expect_raises("chi-square on empty sample rejected",
              lambda: gof.chi_square_uniform_bins([], dist.uniform_cdf,
                                                  dist.uniform_ppf))
expect_raises("KS on empty sample rejected",
              lambda: gof.ks_test([], dist.uniform_cdf))
expect_raises("fit on empty sample rejected",
              lambda: fit.fit_normal([]))

# --------------------------------------- 3. sampling correctness (GoF tests)
section("3. Sampling correctness via distribution-level tests (n=20000)")
N = 20000
cases = [
    ("uniform(-2,5)",
     dist.sample_uniform(random.Random(7), -2, 5, N),
     lambda: dict(cdf=lambda x: dist.uniform_cdf(x, -2, 5),
                  ppf=lambda p: dist.uniform_ppf(p, -2, 5))),
    ("exponential(1.5)",
     dist.sample_exponential(random.Random(8), 1.5, N),
     lambda: dict(cdf=lambda x: dist.exponential_cdf(x, 1.5),
                  ppf=lambda p: dist.exponential_ppf(p, 1.5))),
    ("normal(10,2)",
     dist.sample_normal(random.Random(9), 10, 2, N),
     lambda: dict(cdf=lambda x: dist.normal_cdf(x, 10, 2),
                  ppf=lambda p: 10 + 2 * gof_norm_ppf(p))),
]
from probdist.special import norm_ppf as gof_norm_ppf  # noqa: E402

for label, samples, fns in cases:
    f = fns()
    r1 = gof.chi_square_uniform_bins(samples, f["cdf"], f["ppf"])
    r2 = gof.ks_test(samples, f["cdf"])
    r3 = gof.quantile_check(samples, f["ppf"])
    print(f"-- {label}")
    print(f"   {r1}")
    print(f"   {r2}")
    print(f"   {r3}")
    check(f"{label} chi-square passes", r1.passed, f"stat={r1.statistic:.3f} crit={r1.critical_value:.3f}")
    check(f"{label} KS passes", r2.passed, f"D={r2.statistic:.4f} crit={r2.critical_value:.4f}")
    check(f"{label} quantile check passes", r3.passed)

binom_samples = dist.sample_binomial(random.Random(10), 20, 0.3, N)
pmf = {k: dist.binomial_pmf(k, 20, 0.3) for k in range(21)}
rb = gof.chi_square_discrete(binom_samples, pmf)
print(f"-- binomial(20,0.3)\n   {rb}")
check("binomial(20,0.3) chi-square passes", rb.passed,
      f"stat={rb.statistic:.3f} crit={rb.critical_value:.3f}")

# --------------------------------------- 4. wrong distribution is rejected
section("4. Mismatched samples are rejected (with evidence)")
mismatches = [
    ("exponential(1.5) data vs normal(0.66,0.66) hypothesis",
     dist.sample_exponential(random.Random(11), 1.5, N),
     dict(cdf=lambda x: dist.normal_cdf(x, 1/1.5, 1/1.5),
          ppf=lambda p: 1/1.5 + (1/1.5) * gof_norm_ppf(p))),
    ("uniform(0,1) data vs exponential(1) hypothesis",
     dist.sample_uniform(random.Random(12), 0, 1, N),
     dict(cdf=lambda x: dist.exponential_cdf(x, 1.0),
          ppf=lambda p: dist.exponential_ppf(p, 1.0))),
    ("normal data vs uniform hypothesis",
     dist.sample_normal(random.Random(13), 0, 1, N),
     dict(cdf=lambda x: dist.uniform_cdf(x, -4, 4),
          ppf=lambda p: dist.uniform_ppf(p, -4, 4))),
]
for label, samples, f in mismatches:
    r = gof.chi_square_uniform_bins(samples, f["cdf"], f["ppf"])
    print(f"-- {label}\n   {r}")
    check(f"rejected: {label}", not r.passed,
          f"stat={r.statistic:.1f} >> crit={r.critical_value:.1f}, p={r.p_value:.2e}")

# ------------------------------------------------ 5. parameter fitting
section("5. Parameter recovery and fit quality")
fit_cases = [
    ("uniform", dist.sample_uniform(random.Random(21), 3.0, 9.0, N),
     fit.fit_uniform, {"low": 3.0, "high": 9.0}),
    ("exponential", dist.sample_exponential(random.Random(22), 2.5, N),
     fit.fit_exponential, {"lam": 2.5}),
    ("normal", dist.sample_normal(random.Random(23), -4.0, 0.5, N),
     fit.fit_normal, {"mu": -4.0, "sigma": 0.5}),
    ("binomial", dist.sample_binomial(random.Random(24), 30, 0.4, N),
     lambda s: fit.fit_binomial(s, trials=30), {"trials": 30, "p": 0.4}),
]
for label, samples, fitter, truth in fit_cases:
    res = fitter(samples)
    print(f"-- {label}\n   {res}")
    check(f"fit {label}: GoF passes", res.gof.passed)
    for k, v in truth.items():
        est = res.params[k]
        tol = max(abs(v) * 0.05, 0.02)
        check(f"fit {label}: {k}={est:.4g} within 5% of {v}",
              abs(est - v) <= tol)

print("-- fit binomial with unknown trials (moment estimate)")
res = fit.fit_binomial(dist.sample_binomial(random.Random(25), 15, 0.6, N))
print(f"   {res}")
check("binomial trials recovered", res.params["trials"] == 15,
      f"estimated trials={res.params['trials']}")

print("-- mismatched fit: exponential data fitted as normal must be rejected")
bad = fit.fit_normal(dist.sample_exponential(random.Random(26), 1.0, N))
print(f"   {bad}")
check("normal fit to exponential data rejected", not bad.gof.passed,
      f"stat={bad.gof.statistic:.1f} >> crit={bad.gof.critical_value:.1f}, "
      f"p={bad.gof.p_value:.2e}")

print("-- mismatched fit: normal data fitted as exponential must be rejected")
bad2 = fit.fit_exponential([abs(x) for x in dist.sample_normal(random.Random(27), 0, 1, N)])
print(f"   {bad2}")
check("exponential fit to |normal| data rejected", not bad2.gof.passed,
      f"stat={bad2.gof.statistic:.1f} >> crit={bad2.gof.critical_value:.1f}")

# --------------------------------------------- 6. small samples and tails
section("6. Small samples (single digits) and tail behaviour")
tiny = dist.sample_normal(random.Random(31), 0, 1, 5)
r = gof.chi_square_uniform_bins(tiny, lambda x: dist.normal_cdf(x, 0, 1),
                                lambda p: gof_norm_ppf(p))
print(f"-- n=5 chi-square\n   {r}")
check("n=5 emits small-sample warning", any("small" in w for w in r.warnings))
check("n=5 emits expected-count warning",
      any("expected count" in w for w in r.warnings))

print("-- heavy-tail contamination: mean/var alone cannot see it")
clean = dist.sample_normal(random.Random(32), 0, 1, 5000)
contaminated = clean[:]
rng = random.Random(33)
for i in range(0, 5000, 100):  # 1% of points replaced by extreme outliers
    contaminated[i] = 30.0 * (1 if rng.random() < 0.5 else -1)
mq = gof.quantile_check(contaminated, lambda p: gof_norm_ppf(p))
print(f"   {mq}")
check("quantile check flags contaminated tails", not mq.passed)

# ----------------------------- 7. decision-rule / field semantic consistency
section("7. Decision rule: statistic > critical <=> rejected, p <= alpha <=> rejected")
good = dist.sample_normal(random.Random(9), 10, 2, N)  # passes in section 3
consistency_results = [
    ("chi-square (pass)", gof.chi_square_uniform_bins(
        good, lambda x: dist.normal_cdf(x, 10, 2),
        lambda p: 10 + 2 * gof_norm_ppf(p))),
    ("KS (pass)", gof.ks_test(good, lambda x: dist.normal_cdf(x, 10, 2))),
    ("quantile-check (pass)",
     gof.quantile_check(good, lambda p: 10 + 2 * gof_norm_ppf(p))),
    ("quantile-check (fail)", mq),
    ("chi-square discrete (pass)", rb),
    ("chi-square (fail)", bad.gof),
]
for label, r in consistency_results:
    check(f"{label}: statistic>critical <=> not passed",
          (r.statistic > r.critical_value) == (not r.passed),
          f"stat={r.statistic:.6g} crit={r.critical_value:.6g} passed={r.passed}")
    check(f"{label}: p-value<=alpha <=> not passed",
          (r.p_value <= r.alpha) == (not r.passed),
          f"p={r.p_value:.4g} alpha={r.alpha} passed={r.passed}")

# ------------------------------------------------------------- summary
print("\n=== SUMMARY ===")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f_ in FAILURES:
        print(f"  - {f_}")
    sys.exit(1)
print("all checks passed")
